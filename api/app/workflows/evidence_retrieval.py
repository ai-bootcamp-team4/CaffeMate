import asyncio
import hashlib
from collections import defaultdict
from copy import deepcopy
from datetime import date
from typing import Any, Protocol

import rfc8785

from app.contracts.schema_registry import ContractRegistry, McpContractValidator
from app.domain.errors import ContractValidationError
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.failure_policy import StageExecutionFailurePolicy
from app.workflows.models import HeadFence, StageControl
from app.workflows.stage_context import StageContext

MAX_CONCURRENT_MCP_CALLS = 8
MCP_TIMEOUT_SECONDS = 30.0
RAG_DOCUMENT_TOOLS = frozenset({"retrieve_official_documents", "retrieve_project_documents"})
STRUCTURED_METRIC_TOOLS = frozenset({"get_area_profile", "search_cafe_observations"})


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
        claims = self._claims_by_id(evidence_plan)
        executed, failed, physical_call_count = asyncio.run(
            self._execute_actions(context=context, actions=actions, claims=claims)
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
            raise ContractValidationError("EVIDENCE_RETRIEVAL requires a complete Evidence Plan")
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
                        raise ContractValidationError("Evidence Plan action context is invalid")
                    tool_name = action.get("tool_name")
                    arguments = action.get("typed_arguments")
                    if not isinstance(tool_name, str) or tool_name not in allowed_tools:
                        raise ContractValidationError("Evidence Plan tool is not allowed")
                    if not isinstance(arguments, dict):
                        raise ContractValidationError("Evidence Plan arguments are invalid")
                    self._contracts.validate_mcp_tool_input(tool_name, arguments)
                    if action.get("tool_version") != self._contracts.mcp_tool_version(tool_name):
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

    @staticmethod
    def _claims_by_id(evidence_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        claims: dict[str, dict[str, Any]] = {}
        for value in evidence_plan["claims"]:
            if not isinstance(value, dict):
                raise ContractValidationError("Evidence Claim is invalid")
            claim_id = value.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id or claim_id in claims:
                raise ContractValidationError("Evidence Claim id is invalid")
            claims[claim_id] = value
        return claims

    async def _execute_actions(
        self,
        *,
        context: StageContext,
        actions: list[dict[str, Any]],
        claims: dict[str, dict[str, Any]],
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
                if not StageExecutionFailurePolicy.can_degrade(error):
                    raise
                return [], [
                    self._failed_action(action, error.mcp_code) for action in grouped_actions
                ]
            return (
                [
                    self._executed_action(
                        action,
                        outcome,
                        claim=claims[action["claim_id"]],
                        project_id=context.project_id,
                    )
                    for action in grouped_actions
                ],
                [],
            )

        results = await asyncio.gather(*(execute_group(group) for group in grouped.values()))
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

    def _executed_action(
        self,
        action: dict[str, Any],
        outcome: McpCallOutcome,
        *,
        claim: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        structured_result = deepcopy(outcome.structured_content)
        if action["tool_name"] in RAG_DOCUMENT_TOOLS:
            self._attach_rag_evidence_candidates(
                structured_result,
                action=action,
                claim=claim,
                project_id=project_id,
            )
            self._contracts.validate_mcp_tool_result(action["tool_name"], structured_result)
        elif action["tool_name"] in STRUCTURED_METRIC_TOOLS:
            self._attach_metric_evidence_candidates(
                structured_result,
                action=action,
                claim=claim,
                project_id=project_id,
            )
            self._contracts.validate_mcp_tool_result(action["tool_name"], structured_result)
        return {
            "action_id": action["action_id"],
            "claim_id": action["claim_id"],
            "polarity": action["polarity"],
            "tool_name": action["tool_name"],
            "request_id": outcome.request_id,
            "structured_result": structured_result,
        }

    @classmethod
    def _attach_metric_evidence_candidates(
        cls,
        structured_result: dict[str, Any],
        *,
        action: dict[str, Any],
        claim: dict[str, Any],
        project_id: str,
    ) -> None:
        metrics = structured_result.get("data")
        source_trace = structured_result.get("source_trace")
        observed_at = structured_result.get("observed_at")
        if not isinstance(metrics, list) or not isinstance(source_trace, list):
            raise ContractValidationError("Structured metric result is invalid")
        if not isinstance(observed_at, str):
            raise ContractValidationError("Structured metric observation time is invalid")

        records: list[dict[str, Any]] = []
        unmapped_metric = False
        for metric in metrics:
            if not isinstance(metric, dict):
                raise ContractValidationError("Structured metric is invalid")
            trace = cls._source_trace_for_metric(metric, source_trace)
            if trace is None:
                unmapped_metric = True
                continue
            records.append(
                cls._metric_evidence_record(
                    metric,
                    trace=trace,
                    action=action,
                    claim=claim,
                    project_id=project_id,
                    observed_at=observed_at,
                )
            )

        structured_result["evidence_records"] = records
        if unmapped_metric:
            missing = structured_result.get("missing_fields")
            if not isinstance(missing, list):
                raise ContractValidationError("Structured metric missing fields are invalid")
            structured_result["missing_fields"] = sorted(
                {*missing, "metric_source_trace"}
            )
            structured_result["status"] = "PARTIAL"

    @staticmethod
    def _source_trace_for_metric(
        metric: dict[str, Any], source_trace: list[Any]
    ) -> dict[str, Any] | None:
        evidence_id = metric.get("evidence_id")
        if not isinstance(evidence_id, str):
            return None
        source_id = evidence_id.split(":", maxsplit=1)[0]
        matching = [
            value
            for value in source_trace
            if isinstance(value, dict) and value.get("source_id") == source_id
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    @classmethod
    def _metric_evidence_record(
        cls,
        metric: dict[str, Any],
        *,
        trace: dict[str, Any],
        action: dict[str, Any],
        claim: dict[str, Any],
        project_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        claim_type = claim.get("claim_type")
        geographic_scope = claim.get("geographic_scope")
        metric_name = metric.get("metric")
        value = metric.get("value")
        unit = metric.get("unit")
        source_date = metric.get("as_of")
        retrieval_evidence_id = metric.get("evidence_id")
        if not isinstance(claim_type, str) or not isinstance(geographic_scope, dict):
            raise ContractValidationError("Structured metric Claim context is invalid")
        if (
            not isinstance(metric_name, str)
            or not metric_name
            or not isinstance(value, dict)
            or (unit is not None and not isinstance(unit, str))
            or not isinstance(source_date, str)
            or not isinstance(retrieval_evidence_id, str)
            or not retrieval_evidence_id
        ):
            raise ContractValidationError("Structured metric fields are invalid")
        source_id = trace.get("source_id")
        source_ref = trace.get("source_ref")
        checksum = trace.get("content_digest")
        if (
            not isinstance(source_id, str)
            or not source_id
            or not isinstance(source_ref, str)
            or not source_ref
            or not isinstance(checksum, str)
            or not checksum
        ):
            raise ContractValidationError("Structured metric source trace is invalid")

        identity = {
            "project_id": project_id,
            "claim_id": action["claim_id"],
            "claim_type": claim_type,
            "metric": metric_name,
            "retrieval_evidence_id": retrieval_evidence_id,
        }
        evidence_digest = hashlib.sha256(rfc8785.dumps(identity)).hexdigest()
        excerpt_digest = hashlib.sha256(rfc8785.dumps(metric)).hexdigest()
        parts = retrieval_evidence_id.split(":", maxsplit=2)
        document_version = parts[1] if len(parts) == 3 else None
        derived = isinstance(unit, str) and "DERIVED" in unit
        missing_context = ["QUARTERLY_ADMIN_DONG_AGGREGATE"]
        if metric_name == "CLOSURE_RATE" and derived:
            missing_context.append("CLOSE_COUNT_DIVIDED_BY_CURRENT_STORE_COUNT")
        return {
            "schema_version": "2.0.0",
            "evidence_id": f"structured-evidence:{evidence_digest}",
            "project_id": project_id,
            "claim_type": claim_type,
            "metric": metric_name,
            "value": deepcopy(value),
            "value_kind": "DERIVED_RESULT" if derived else "EVIDENCED_FACT",
            "unit": unit,
            "geographic_scope": deepcopy(geographic_scope),
            "source": {
                "title": source_id,
                "source_ref": source_ref,
                "authority": "PRIMARY_DATA",
                "source_type": "DATASET",
                "published_or_data_date": source_date,
                "source_observed_at": observed_at,
                "document_version": document_version,
                "checksum": checksum,
            },
            "original_anchor": {
                "anchor_type": "CALCULATION" if derived else "DATASET_ROW",
                "locator": retrieval_evidence_id,
                "excerpt_hash": f"sha256:{excerpt_digest}",
            },
            "freshness_status": cls._freshness_status(
                source_date,
                as_of=action["date_constraints"]["as_of"],
                max_age_days=action["date_constraints"]["max_age_days"],
            ),
            "conflict_status": "NONE",
            "retrieved_at": observed_at,
            "missing_context": missing_context,
            "durable_evidence_refs": [retrieval_evidence_id, source_id],
        }

    @classmethod
    def _attach_rag_evidence_candidates(
        cls,
        structured_result: dict[str, Any],
        *,
        action: dict[str, Any],
        claim: dict[str, Any],
        project_id: str,
    ) -> None:
        hits = structured_result.get("data")
        source_trace = structured_result.get("source_trace")
        observed_at = structured_result.get("observed_at")
        if not isinstance(hits, list) or not isinstance(source_trace, list):
            raise ContractValidationError("RAG Evidence result is invalid")
        if not isinstance(observed_at, str):
            raise ContractValidationError("RAG Evidence observation time is invalid")

        records: list[dict[str, Any]] = []
        unmapped_hit = False
        for hit in hits:
            if not isinstance(hit, dict):
                raise ContractValidationError("RAG Evidence hit is invalid")
            trace = cls._source_trace_for_hit(hit, source_trace)
            if trace is None:
                unmapped_hit = True
                continue
            records.append(
                cls._rag_evidence_record(
                    hit,
                    trace=trace,
                    action=action,
                    claim=claim,
                    project_id=project_id,
                    observed_at=observed_at,
                )
            )

        structured_result["evidence_records"] = records
        if unmapped_hit:
            missing = structured_result.get("missing_fields")
            if not isinstance(missing, list):
                raise ContractValidationError("RAG Evidence missing fields are invalid")
            structured_result["missing_fields"] = sorted({*missing, "rag_hit_source_trace"})
            structured_result["status"] = "PARTIAL"

    @staticmethod
    def _source_trace_for_hit(
        hit: dict[str, Any], source_trace: list[Any]
    ) -> dict[str, Any] | None:
        anchor = hit.get("anchor")
        if not isinstance(anchor, str):
            return None
        valid_traces = [value for value in source_trace if isinstance(value, dict)]
        matching = [
            value
            for value in valid_traces
            if isinstance(value.get("source_ref"), str) and anchor.startswith(value["source_ref"])
        ]
        if len(matching) == 1:
            return matching[0]
        return None

    @classmethod
    def _rag_evidence_record(
        cls,
        hit: dict[str, Any],
        *,
        trace: dict[str, Any],
        action: dict[str, Any],
        claim: dict[str, Any],
        project_id: str,
        observed_at: str,
    ) -> dict[str, Any]:
        claim_type = claim.get("claim_type")
        geographic_scope = claim.get("geographic_scope")
        if not isinstance(claim_type, str) or not isinstance(geographic_scope, dict):
            raise ContractValidationError("RAG Evidence Claim context is invalid")
        excerpt = hit.get("excerpt")
        anchor = hit.get("anchor")
        title = hit.get("title")
        source_date = hit.get("source_date")
        hit_evidence_id = hit.get("evidence_id")
        document_revision_id = hit.get("document_revision_id")
        if (
            not isinstance(excerpt, str)
            or not excerpt
            or not isinstance(anchor, str)
            or not anchor
            or not isinstance(title, str)
            or not title
            or not isinstance(hit_evidence_id, str)
            or not hit_evidence_id
            or not isinstance(document_revision_id, str)
            or not document_revision_id
            or (source_date is not None and not isinstance(source_date, str))
        ):
            raise ContractValidationError("RAG Evidence hit fields are invalid")

        freshness_status = cls._freshness_status(
            source_date,
            as_of=action["date_constraints"]["as_of"],
            max_age_days=action["date_constraints"]["max_age_days"],
        )
        missing_context = [] if source_date is not None else ["SOURCE_DATE_UNKNOWN"]
        source_ref = trace.get("source_ref")
        checksum = trace.get("content_digest")
        if not isinstance(source_ref, str) or not source_ref:
            raise ContractValidationError("RAG Evidence source reference is invalid")
        if not isinstance(checksum, str) or not checksum:
            raise ContractValidationError("RAG Evidence source digest is invalid")

        identity: dict[str, Any] = {
            "project_id": project_id,
            "claim_id": action["claim_id"],
            "claim_type": claim_type,
            "geographic_scope": geographic_scope,
            "retrieval_evidence_id": hit_evidence_id,
            "document_revision_id": document_revision_id,
        }
        evidence_digest = hashlib.sha256(rfc8785.dumps(identity)).hexdigest()
        excerpt_digest = hashlib.sha256(excerpt.encode()).hexdigest()
        official = action["tool_name"] == "retrieve_official_documents"
        return {
            "schema_version": "2.0.0",
            "evidence_id": f"rag-evidence:{evidence_digest}",
            "project_id": project_id,
            "claim_type": claim_type,
            "value": {"kind": "STRING", "value": excerpt},
            "value_kind": "EVIDENCED_FACT",
            "unit": None,
            "geographic_scope": deepcopy(geographic_scope),
            "source": {
                "title": title,
                "source_ref": source_ref,
                "authority": "PRIMARY_OFFICIAL" if official else "USER_ARTIFACT",
                "source_type": "WEB" if official else "USER_DOCUMENT",
                "published_or_data_date": source_date,
                "source_observed_at": observed_at,
                "document_version": document_revision_id,
                "checksum": checksum,
            },
            "original_anchor": {
                "anchor_type": "SECTION",
                "locator": anchor,
                "excerpt_hash": f"sha256:{excerpt_digest}",
            },
            "freshness_status": freshness_status,
            "conflict_status": "NONE",
            "retrieved_at": observed_at,
            "missing_context": missing_context,
            "durable_evidence_refs": [hit_evidence_id, document_revision_id],
        }

    @staticmethod
    def _freshness_status(
        source_date: str | None,
        *,
        as_of: str,
        max_age_days: int | None,
    ) -> str:
        if source_date is None:
            return "UNKNOWN"
        try:
            source = date.fromisoformat(source_date)
            decision_date = date.fromisoformat(as_of)
        except ValueError as error:
            raise ContractValidationError("RAG Evidence date is invalid") from error
        if source > decision_date:
            return "UNKNOWN"
        if max_age_days is None:
            return "NOT_APPLICABLE"
        return "FRESH" if (decision_date - source).days <= max_age_days else "STALE"

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
        statuses = {value["structured_result"].get("status") for value in executed}
        return "COMPLETE" if statuses == {"OK"} else "PARTIAL"
