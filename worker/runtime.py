from collections.abc import Mapping
from enum import StrEnum
from queue import SimpleQueue
from threading import Event, Thread
from time import monotonic
from typing import Protocol

from app.domain.errors import ContractValidationError
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    StageFailure,
    StageLease,
)

from worker.pubsub import PubSubDelivery


class DeliveryOutcome(StrEnum):
    APPLIED = "APPLIED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    DUPLICATE_OR_INELIGIBLE = "DUPLICATE_OR_INELIGIBLE"


class WorkerRetryRequiredError(RuntimeError):
    """Signal a non-2xx Pub/Sub response without exposing provider details."""


class _StageLeaseLostError(RuntimeError):
    pass


class StageProcessor(Protocol):
    def process(self, lease: StageLease) -> dict[str, object]: ...


class StageExecutionRepository(Protocol):
    def claim(
        self,
        *,
        stage_run_id: str,
        worker_id: str,
        expected_input_digest: str,
    ) -> StageLease | None: ...

    def heartbeat(self, *, stage_run_id: str, lease_token: str) -> bool: ...

    def checkpoint(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        result: dict[str, object],
    ) -> CheckpointOutcome: ...

    def record_failure(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        input_digest: str,
        failure: StageFailure,
    ) -> FailureOutcome: ...


class DurableWorker:
    def __init__(
        self,
        execution: StageExecutionRepository,
        processor: StageProcessor,
        *,
        worker_id: str,
        heartbeat_interval_seconds: float = 15.0,
        max_processing_seconds: float = 120.0,
    ) -> None:
        if heartbeat_interval_seconds <= 0:
            raise ValueError("Worker heartbeat interval must be positive")
        if max_processing_seconds <= heartbeat_interval_seconds:
            raise ValueError("Worker processing timeout must exceed heartbeat interval")
        self._execution = execution
        self._processor = processor
        self._worker_id = worker_id
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._max_processing_seconds = max_processing_seconds

    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        if delivery.logical_topic != "WORKFLOW_STAGE_READY":
            raise ValueError(f"Unsupported worker topic: {delivery.logical_topic}")
        stage_run_id = self._required_string(delivery.payload, "stage_run_id")
        input_digest = self._required_string(delivery.payload, "input_digest")
        lease = self._execution.claim(
            stage_run_id=stage_run_id,
            worker_id=self._worker_id,
            expected_input_digest=input_digest,
        )
        if lease is None:
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        try:
            result = self._process_with_heartbeat(lease)
            outcome = self._execution.checkpoint(
                stage_run_id=stage_run_id,
                lease_token=lease.lease_token,
                input_digest=input_digest,
                result=result,
            )
        except _StageLeaseLostError:
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        except Exception as error:
            failure = self._failure_from_exception(error)
            failure_outcome = self._execution.record_failure(
                stage_run_id=stage_run_id,
                lease_token=lease.lease_token,
                input_digest=input_digest,
                failure=failure,
            )
            if failure_outcome == FailureOutcome.RETRY_SCHEDULED:
                raise WorkerRetryRequiredError("Stage retry is required") from error
            if failure_outcome == FailureOutcome.TERMINAL_FAILED:
                return DeliveryOutcome.TERMINAL_FAILED
            return DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        return (
            DeliveryOutcome.APPLIED
            if outcome == CheckpointOutcome.APPLIED
            else DeliveryOutcome.DUPLICATE_OR_INELIGIBLE
        )

    def _process_with_heartbeat(self, lease: StageLease) -> dict[str, object]:
        completed = Event()
        outcome: SimpleQueue[tuple[bool, object]] = SimpleQueue()

        def run_processor() -> None:
            try:
                outcome.put((True, self._processor.process(lease)))
            except Exception as error:  # noqa: BLE001 - transfer to lease owner thread
                outcome.put((False, error))
            finally:
                completed.set()

        Thread(
            target=run_processor,
            name=f"caffemate-stage-{lease.stage_run_id}",
            daemon=True,
        ).start()
        deadline = monotonic() + self._max_processing_seconds
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Stage processing exceeded its bounded runtime")
            if completed.wait(min(self._heartbeat_interval_seconds, remaining)):
                succeeded, value = outcome.get()
                if succeeded:
                    if not isinstance(value, dict):
                        raise TypeError("Stage processor result must be an object")
                    return value
                if isinstance(value, Exception):
                    raise value
                raise RuntimeError("Stage processor raised a non-standard failure")
            if not self._execution.heartbeat(
                stage_run_id=lease.stage_run_id,
                lease_token=lease.lease_token,
            ):
                raise _StageLeaseLostError("Stage lease is no longer current")

    @staticmethod
    def _failure_from_exception(error: Exception) -> StageFailure:
        if isinstance(error, ContractValidationError):
            return StageFailure(code="CONTRACT_REJECTED", retryable=False)
        if isinstance(error, TimeoutError):
            return StageFailure(code="STAGE_TIMEOUT", retryable=True)
        return StageFailure(code="STAGE_PROCESSING_ERROR", retryable=True)

    @staticmethod
    def _required_string(payload: Mapping[str, object], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Worker payload field is invalid: {field}")
        return value
