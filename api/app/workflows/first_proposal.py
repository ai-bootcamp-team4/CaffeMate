"""사용자 분석 요청은 의존 단계 없이 단일 제안 실행으로 표현한다."""

import hashlib
from enum import StrEnum
from typing import Any, cast

import rfc8785

from app.workflows.models import HeadFence


class FirstProposalStage(StrEnum):
    """The first proposal is one bounded unit of work."""

    RUN_PROPOSAL = "RUN_PROPOSAL"


def stage_input_digest(
    *,
    workflow_run_id: str,
    stage_code: FirstProposalStage,
    head: HeadFence,
) -> str:
    return hashlib.sha256(
        rfc8785.dumps(
            cast(
                Any,
                {
                    "workflow_run_id": workflow_run_id,
                    "stage_code": stage_code.value,
                    "head": head.model_dump(mode="json"),
                    "contract_version": "2.0.0",
                },
            )
        )
    ).hexdigest()
