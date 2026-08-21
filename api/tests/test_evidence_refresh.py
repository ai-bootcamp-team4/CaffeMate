from datetime import UTC, datetime

from app.evidence.refresh import EvidenceRefreshService


def record(
    *, source_type: str, observed_at: str | None, status: str = "FRESH"
) -> dict[str, object]:
    return {
        "freshness_status": status,
        "source": {
            "source_type": source_type,
            "source_observed_at": observed_at,
            "published_or_data_date": None,
        },
    }


def test_freshness_policy_is_source_specific_and_missing_date_is_not_fresh() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)

    assert EvidenceRefreshService._is_expired(
        record(source_type="API", observed_at="2026-08-20T23:59:59Z"), now
    )
    assert not EvidenceRefreshService._is_expired(
        record(source_type="DATASET", observed_at="2026-08-01T00:00:00Z"), now
    )
    assert EvidenceRefreshService._is_expired(
        record(source_type="WEB", observed_at=None), now
    )
    assert not EvidenceRefreshService._is_expired(
        record(source_type="USER_DOCUMENT", observed_at=None), now
    )
    assert not EvidenceRefreshService._is_expired(
        record(source_type="API", observed_at=None, status="NOT_APPLICABLE"), now
    )
