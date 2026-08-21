import asyncio
import hashlib
from collections import defaultdict
from typing import Any, Protocol

import rfc8785

from app.contracts.schema_registry import ContractRegistry, McpContractValidator
from app.domain.errors import ContractValidationError
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.models import HeadFence, StageControl
from app.workflows.stage_context import StageContext

MAX_CONCURRENT_MCP_CALLS = 8
MCP_TIMEOUT_SECONDS = 30.0


class EvidenceMcpClient(Protocol):
    async def call_tool(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        tool_name: str,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        timeout_seconds: float = MCP_TIMEOUT_SECONDS,
    ) -> McpCallOutcome: ...


class EvidenceRetrievalStageHandler:
    def __init__(
        self,
        mcp_client: EvidenceMcpClient,
        *,
        contracts: McpContractValidator | None = None,
        max_concurrency: int = MAX_CONCURRENT_MCP_CALLS,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("MCP concurrency must be positive")
        self._mcp_client = mcp_client
        self._contracts = contracts or ContractRegistry()
        self._max_concurrency = max_concurrency

    def execute(self, context: StageContext) -> dict[str, object]:
        evidence_plan = self._load_plan(context)
        actions = self._validate_actions(evidence_plan)
        executed, failed, physical_call_count = asyncio.run(
            self._execute_actions(context=context, actions=actions)
        )
        completeness = self._completeness(
            planned=len(actions),
            executed=executed,
            failed=failed,
        )
        return {
            "stage_control": StageControl().model_dump(mode="json"),
            "evidence_retrieval": {
                "claims": evidence_plan["claims"],
                "planned_action_count": len(actions),
                "physical_call_count": physical_call_count,
                "completeness": completeness,
                "executed_actions": executed,
                "failed_actions": failed,
            },
        }

    @staticmethod
    def _load_plan(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("EVIDENCE_PLAN")
        value = dependency.get("evidence_plan") if dependency else None
        if not isinstance(value, dict) or value.get("status") != "COMPLETE":
            raise ContractValidationError(
                "EVIDENCE_RETRIEVAL requires a complete Evidence Plan"
            )
        if not isinstance(value.get("claims"), list) or not value["claims"]:
            raise ContractValidationError("Evidence Plan claims are missing")
        if not isinstance(value.get("claim_plans"), list) or not value["claim_plans"]:
            raise ContractValidationError("Evidence Plan actions are missing")
        return value

    def _validate_actions(self, evidence_plan: dict[str, Any]) -> list[dict[str, Any]]:
        claims = {
            claim.get("claim_id")
            for claim in evidence_plan["claims"]
            if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
        }
        constraints = evidence_plan.get("planning_constraints")
        if not isinstance(constraints, dict):
            raise ContractValidationError("Evidence planning constraints are missing")
        allowed_tools = set(constraints.get("allowed_tools", []))
        max_total = constraints.get("max_total_actions")
        max_per_claim = constraints.get("max_actions_per_claim")
        actions: list[dict[str, Any]] = []
        for plan in evidence_plan["claim_plans"]:
            if not isinstance(plan, dict) or plan.get("claim_id") not in claims:
                raise ContractValidationError("Evidence Plan contains an unknown claim")
            for field, polarity in (
                ("support_actions", "SUPPORT"),
                ("counter_actions", "COUNTER"),
            ):
                values = plan.get(field)
                if not isinstance(values, list):
                    raise ContractValidationError("Evidence Plan action list is invalid")
                for value in values:
                    if not isinstance(value, dict):
                        raise ContractValidationError("Evidence Plan action is invalid")
                    action = dict(value)
                    if (
                        action.get("claim_id") != plan["claim_id"]
                        or action.get("polarity") != polarity
                    ):
                        raise ContractValidationError(
                            "Evidence Plan action context is invalid"
                        )
                    tool_name = action.get("tool_name")
                    arguments = action.get("typed_arguments")
                    if not isinstance(tool_name, str) or tool_name not in allowed_tools:
                        raise ContractValidationError("Evidence Plan tool is not allowed")
                    if not isinstance(arguments, dict):
                        raise ContractValidationError("Evidence Plan arguments are invalid")
                    self._contracts.validate_mcp_tool_input(tool_name, arguments)
                    if action.get("tool_version") != self._contracts.mcp_tool_version(
                        tool_name
                    ):
                        raise ContractValidationError("Evidence Plan tool version changed")
                    actions.append(action)

        action_ids = [action.get("action_id") for action in actions]
        if any(not isinstance(value, str) or not value for value in action_ids):
            raise ContractValidationError("Evidence Plan action id is invalid")
        if len(action_ids) != len(set(action_ids)):
            raise ContractValidationError("Evidence Plan action ids are duplicated")
        if not isinstance(max_total, int) or len(actions) > max_total:
            raise ContractValidationError("Evidence Plan exceeds the total action limit")
        counts: dict[str, int] = defaultdict(int)
        for action in actions:
            counts[action["claim_id"]] += 1
        if not isinstance(max_per_claim, int) or any(
            count > max_per_claim for count in counts.values()
        ):
            raise ContractValidationError("Evidence Plan exceeds the per-claim action limit")
        return actions

    async def _execute_actions(
        self,
        *,
        context: StageContext,
        actions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for action in actions:
            grouped[self._call_digest(action)].append(action)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def execute_group(
            grouped_actions: list[dict[str, Any]],
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            representative = grouped_actions[0]
            try:
                async with semaphore:
                    outcome = await self._mcp_client.call_tool(
                        venture_project_id=context.project_id,
                        workflow_run_id=context.lease.workflow_run_id,
                        head=context.lease.head,
                        tool_name=representative["tool_name"],
                        arguments=representative["typed_arguments"],
                        timeout_seconds=MCP_TIMEOUT_SECONDS,
                    )
            except McpClientError as error:
                return [], [
                    self._failed_action(action, error.mcp_code)
                    for action in grouped_actions
                ]
            return (
                [self._executed_action(action, outcome) for action in grouped_actions],
                [],
            )

        results = await asyncio.gather(
            *(execute_group(group) for group in grouped.values())
        )
        executed = [item for success, _ in results for item in success]
        failed = [item for _, failures in results for item in failures]
        executed.sort(key=lambda value: value["action_id"])
        failed.sort(key=lambda value: value["action_id"])
        return executed, failed, len(grouped)

    @staticmethod
    def _call_digest(action: dict[str, Any]) -> str:
        value = {
            "tool_name": action["tool_name"],
            "tool_version": action["tool_version"],
            "typed_arguments": action["typed_arguments"],
        }
        return hashlib.sha256(rfc8785.dumps(value)).hexdigest()

    @staticmethod
    def _executed_action(
        action: dict[str, Any],
        outcome: McpCallOutcome,
    ) -> dict[str, Any]:
        return {
            "action_id": action["action_id"],
            "claim_id": action["claim_id"],
            "polarity": action["polarity"],
            "tool_name": action["tool_name"],
            "request_id": outcome.request_id,
            "structured_result": outcome.structured_content,
        }

    @staticmethod
    def _failed_action(action: dict[str, Any], code: str) -> dict[str, Any]:
        return {
            "action_id": action["action_id"],
            "claim_id": action["claim_id"],
            "polarity": action["polarity"],
            "tool_name": action["tool_name"],
            "error_code": code,
        }

    @staticmethod
    def _completeness(
        *,
        planned: int,
        executed: list[dict[str, Any]],
        failed: list[dict[str, Any]],
    ) -> str:
        if failed or len(executed) != planned:
            return "UNAVAILABLE" if not executed else "PARTIAL"
        statuses = {
            value["structured_result"].get("status") for value in executed
        }
        return "COMPLETE" if statuses == {"OK"} else "PARTIAL"
