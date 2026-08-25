from typing import Any

from sqlalchemy import Engine, text

from app.candidates.seed_registry import IndependentSeedRegistry
from app.workflows.execution import PostgresFirstProposalExecutor
from app.workflows.lease import PostgresWorkflowLeaseRepository
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.progress import FirstProposalProgressStage
from app.workflows.service import WorkflowService
from app.workflows.simple_proposal import SimpleProposalBuilder


class IdentityFixture:
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "test-token"
        return "user-2"


class PublishFuture:
    def result(self, timeout: float | None = None) -> str:
        assert timeout == 10.0
        return "pubsub-message-1"


class PublisherFixture:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, dict[str, str]]] = []

    def publish(self, topic: str, data: bytes, **attributes: str) -> PublishFuture:
        self.messages.append((topic, data, attributes))
        return PublishFuture()


class RepositoryPipeline:
    """Persistence tests keep workflow execution isolated from external Agent and MCP calls."""

    def __init__(self, registry: IndependentSeedRegistry) -> None:
        self._builder = SimpleProposalBuilder(registry)

    def run(self, **kwargs: Any) -> object:
        progress = kwargs.get("progress")
        for stage in (
            FirstProposalProgressStage.EVIDENCE_RETRIEVAL,
            FirstProposalProgressStage.EVIDENCE_ASSESS,
            FirstProposalProgressStage.PROPOSAL_GENERATION,
        ):
            if progress is not None:
                progress.start(stage)
                progress.complete(stage)
        if progress is not None:
            progress.start(FirstProposalProgressStage.FINANCE_AND_RANK)
        bundle = self._builder.build(
            state=kwargs["state"],
            evidence_records=kwargs["evidence_records"],
            property_cost_override=kwargs.get("property_cost_override"),
            franchise_universe=[
                {
                    "brand_id": "kr-ediya-coffee",
                    "display_name": "이디야커피",
                    "individual_franchise_eligibility": "VERIFIED",
                    "evidence_refs": ["franchise-eligibility:ediya"],
                    "finance_profile": {
                        "currency": "KRW",
                        "coverage": "PARTIAL",
                        "value_kind": "EVIDENCED_FACT",
                        "known_initial_cost_range_krw": {
                            "low": 27_000_000,
                            "base": 27_000_000,
                            "high": 27_000_000,
                        },
                        "reference_area_sqm": None,
                        "monthly_royalty_krw": 250_000,
                        "evidence_refs": ["franchise-cost:ediya"],
                        "source_refs": ["https://example.com/ediya"],
                        "scope_note": "repository test fixture",
                        "missing_costs": [
                            "DEPOSIT",
                            "ACQUISITION_OR_PREMIUM",
                            "CONSTRUCTION",
                            "EQUIPMENT",
                            "OPERATING_RESERVE",
                        ],
                    },
                }
            ],
        )
        if progress is not None:
            progress.complete(FirstProposalProgressStage.FINANCE_AND_RANK)
            progress.start(FirstProposalProgressStage.CANDIDATE_AUDIT)
            progress.complete(FirstProposalProgressStage.CANDIDATE_AUDIT)
        return bundle


def workflow_service(engine: Engine) -> WorkflowService:
    registry = IndependentSeedRegistry.load_default()
    return WorkflowService(
        PostgresWorkflowRepository(
            engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            pipeline=RepositoryPipeline(registry),
            seed_registry=registry,
        )
    )


def execute_queued_workflow(engine: Engine, workflow_run_id: str) -> None:
    with engine.connect() as connection:
        execution = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
            ),
            {"workflow_run_id": workflow_run_id},
        ).mappings().one()
    leases = PostgresWorkflowLeaseRepository(engine)
    lease = leases.claim(
        stage_run_id=execution["stage_run_id"],
        worker_id="test-worker",
        expected_input_digest=execution["input_digest"],
    )
    assert lease is not None
    registry = IndependentSeedRegistry.load_default()
    PostgresFirstProposalExecutor(
        engine,
        RepositoryPipeline(registry),
        leases,
    ).execute(lease)
