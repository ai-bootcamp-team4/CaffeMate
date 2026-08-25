from collections.abc import Mapping
from enum import StrEnum
from queue import SimpleQueue
from threading import Event, Thread
from time import monotonic
from typing import Protocol

from app.workflows.models import FailureOutcome, StageFailure, StageLease

from worker.errors import StageExecutionError
from worker.pubsub import PubSubDelivery


class DeliveryOutcome(StrEnum):
    APPLIED = "APPLIED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    DUPLICATE_OR_INELIGIBLE = "DUPLICATE_OR_INELIGIBLE"


class WorkerRetryRequiredError(RuntimeError):
    pass


class _StageLeaseLostError(RuntimeError):
    pass


class WorkflowProcessor(Protocol):
    def process(self, lease: StageLease) -> dict[str, object]: ...


class WorkflowLeaseRepository(Protocol):
    def claim(
        self,
        *,
        stage_run_id: str,
        worker_id: str,
        expected_input_digest: str,
    ) -> StageLease | None: ...

    def heartbeat(self, *, stage_run_id: str, lease_token: str) -> bool: ...

    def is_pending_delivery(
        self,
        *,
        stage_run_id: str,
        expected_input_digest: str,
    ) -> bool: ...

    def record_failure(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        failure: StageFailure,
    ) -> FailureOutcome: ...


class DurableWorkflowWorker:
    def __init__(
        self,
        execution: WorkflowLeaseRepository,
        processor: WorkflowProcessor,
        *,
        worker_id: str,
        heartbeat_interval_seconds: float = 15.0,
        max_processing_seconds: float = 600.0,
    ) -> None:
        self._execution = execution
        self._processor = processor
        self._worker_id = worker_id
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._max_processing_seconds = max_processing_seconds

    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        if delivery.logical_topic != "WORKFLOW_STAGE_READY":
            raise ValueError(f"Unsupported workflow topic: {delivery.logical_topic}")
        stage_run_id = self._required_string(delivery.payload, "stage_run_id")
        input_digest = self._required_string(delivery.payload, "input_digest")
        lease = self._execution.claim(
            stage_run_id=stage_run_id,
            worker_id=self._worker_id,
            expected_input_digest=input_digest,
        )
        if lease is None:
            if self._execution.is_pending_delivery(
                stage_run_id=stage_run_id,
                expected_input_digest=input_digest,
            ):
                raise WorkerRetryRequiredError("Workflow lease is temporarily unavailable")
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        try:
            self._process_with_heartbeat(lease)
        except _StageLeaseLostError as error:
            if self._execution.is_pending_delivery(
                stage_run_id=stage_run_id,
                expected_input_digest=input_digest,
            ):
                raise WorkerRetryRequiredError("Workflow lease was lost before completion") from error
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        except Exception as error:
            outcome = self._execution.record_failure(
                stage_run_id=stage_run_id,
                lease_token=lease.lease_token,
                input_digest=input_digest,
                failure=self._failure_from_exception(error),
            )
            if outcome == FailureOutcome.RETRY_SCHEDULED:
                raise WorkerRetryRequiredError("Workflow stage retry is required") from error
            if outcome == FailureOutcome.TERMINAL_FAILED:
                return DeliveryOutcome.TERMINAL_FAILED
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        return DeliveryOutcome.APPLIED

    def _process_with_heartbeat(self, lease: StageLease) -> None:
        completed = Event()
        outcome: SimpleQueue[Exception | None] = SimpleQueue()

        def run_processor() -> None:
            try:
                self._processor.process(lease)
                outcome.put(None)
            except Exception as error:  # noqa: BLE001 - transfer to lease owner thread
                outcome.put(error)
            finally:
                completed.set()

        Thread(
            target=run_processor,
            name=f"caffemate-workflow-{lease.stage_run_id}",
            daemon=True,
        ).start()
        deadline = monotonic() + self._max_processing_seconds
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Workflow processing exceeded its bounded runtime")
            if completed.wait(min(self._heartbeat_interval_seconds, remaining)):
                error = outcome.get()
                if error is not None:
                    raise error
                return
            if not self._execution.heartbeat(
                stage_run_id=lease.stage_run_id,
                lease_token=lease.lease_token,
            ):
                raise _StageLeaseLostError("Workflow lease is no longer current")

    @staticmethod
    def _failure_from_exception(error: Exception) -> StageFailure:
        if isinstance(error, StageExecutionError):
            return StageFailure(code=error.code, retryable=error.retryable)
        if isinstance(error, TimeoutError):
            return StageFailure(code="WORKFLOW_TIMEOUT", retryable=True)
        return StageFailure(code="WORKFLOW_PROCESSING_ERROR", retryable=True)

    @staticmethod
    def _required_string(payload: Mapping[str, object], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Workflow payload field is invalid: {field}")
        return value