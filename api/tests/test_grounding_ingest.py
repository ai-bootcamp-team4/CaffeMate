from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from typing import Any

import httpx
import pytest

from app.grounding.bigquery import BigQueryLoad, BigQueryRestClient
from app.grounding.seoul_ingest import (
    CAFE_INDUSTRY_CODE,
    SeoulOpenApiClient,
    approved_ingestion_exists,
    candidate_quarters,
    latest_period_rows,
    normalize_cafe_sales,
    normalize_cafe_store,
    normalize_mapping,
    normalize_populations,
    parse_mois_mapping,
    upload_immutable,
    validate_normalized,
)


class StaticTokenProvider:
    def access_token(self) -> str:
        return "test-token"


def test_candidate_quarters_crosses_year_boundary() -> None:
    assert candidate_quarters(date(2026, 2, 1), count=4) == [
        "20261",
        "20254",
        "20253",
        "20252",
    ]


def test_seoul_client_paginates_without_leaking_key_in_errors() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        start = int(request.url.path.split("/")[-2])
        if start == 1:
            rows = [{"id": index} for index in range(1, 1001)]
        else:
            rows = [{"id": index} for index in range(1001, 1003)]
        return httpx.Response(
            200,
            json={"Sample": {"list_total_count": 1002, "row": rows}},
        )

    client = SeoulOpenApiClient(
        api_key="secret-value",
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    assert len(client.fetch_rows("Sample")) == 1002
    assert len(calls) == 2

    def failure(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="failed")

    failing = SeoulOpenApiClient(
        api_key="secret-value",
        base_url="https://example.test",
        transport=httpx.MockTransport(failure),
    )
    with pytest.raises(RuntimeError) as error:
        failing.fetch_rows("Sample")
    assert "secret-value" not in repr(error.value)


def test_mois_mapping_parser_and_admin_code_normalization() -> None:
    line = bytearray(b" " * 163)
    _put_cp949(line, 0, 10, "1144069000")
    _put_cp949(line, 11, 42, "서울특별시")
    _put_cp949(line, 42, 73, "마포구")
    _put_cp949(line, 73, 104, "망원제1동")
    _put_cp949(line, 104, 114, "1144012300")
    _put_cp949(line, 115, 146, "망원동")
    _put_cp949(line, 146, 154, "20080707")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("KIKmix.20260701", bytes(line) + b"\r\n")

    revision, parsed = parse_mois_mapping(archive.getvalue())
    assert revision == "20260701"
    assert parsed == [
        {
            "admin_dong_code": "1144069000",
            "admin_dong_name": "망원제1동",
            "legal_dong_code": "1144012300",
            "legal_dong_name": "망원동",
            "created_date": "2008-07-07",
            "deleted_date": None,
        }
    ]
    assert normalize_mapping(
        parsed,
        ingestion_id="ingest",
        source_revision=revision,
        loaded_at="2026-08-23T00:00:00Z",
    )[0]["admin_dong_code"] == "11440690"


def test_normalizers_keep_only_cafe_and_latest_population_period() -> None:
    store_rows = [
        _store_row(CAFE_INDUSTRY_CODE, "커피-음료"),
        _store_row("CS100001", "한식음식점"),
    ]
    sales_rows = [
        {
            **_base_row(CAFE_INDUSTRY_CODE, "커피-음료"),
            "THSMON_SELNG_AMT": 123456,
            "THSMON_SELNG_CO": 789.0,
        },
        {
            **_base_row("CS100001", "한식음식점"),
            "THSMON_SELNG_AMT": 1,
            "THSMON_SELNG_CO": 1,
        },
    ]
    store = normalize_cafe_store(store_rows, ingestion_id="i", loaded_at="now")
    sales = normalize_cafe_sales(sales_rows, ingestion_id="i", loaded_at="now")
    assert len(store) == 1
    assert store[0]["closure_count"] == 2.0
    assert len(sales) == 1
    assert sales[0]["estimated_sales_krw"] == 123456

    foot_rows = [
        _population_row("20254", "FLPOP", "TOT_FLPOP_CO", 90),
        _population_row("20261", "FLPOP", "TOT_FLPOP_CO", 100),
    ]
    population = normalize_populations(
        {"FOOT_TRAFFIC": latest_period_rows(foot_rows)},
        ingestion_id="i",
        loaded_at="now",
    )
    assert population[0]["period_code"] == "20261"
    assert population[0]["age_20_count"] == 20.0


def test_quality_gate_rejects_period_mismatch() -> None:
    normalized: dict[str, list[dict[str, Any]]] = {
        "area_mapping": [
            {
                "admin_dong_code": "11440690",
                "deleted_date": None,
            }
        ],
        "seoul_cafe_store_fact": [{"period_code": "20254", "admin_dong_code": "11440690"}],
        "seoul_cafe_sales_fact": [{"period_code": "20261", "admin_dong_code": "11440690"}],
        "seoul_population_fact": [{"period_code": "20261", "admin_dong_code": "11440690"}],
    }
    with pytest.raises(RuntimeError, match="GROUNDING_PERIOD_MISMATCH"):
        validate_normalized(normalized, periods={"store": "20261", "sales": "20261"})


def test_bigquery_load_waits_for_completed_job() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"jobReference": {"projectId": "proj-test", "jobId": "job_1"}},
            )
        return httpx.Response(200, json={"status": {"state": "DONE"}})

    client = BigQueryRestClient(
        project_id="proj-test",
        dataset_id="grounding",
        location="asia-northeast3",
        token_provider=StaticTokenProvider(),
        transport=httpx.MockTransport(handler),
    )
    client.load_ndjson(
        load=BigQueryLoad(
            table="facts",
            source_uri="gs://bucket/facts.ndjson",
            schema=({"name": "id", "type": "STRING", "mode": "REQUIRED"},),
        ),
        job_id="job_1",
    )
    assert [request.method for request in requests] == ["POST", "GET"]
    body = json.loads(requests[0].content)
    assert body["configuration"]["load"]["writeDisposition"] == "WRITE_APPEND"
    assert requests[0].headers["Authorization"] == "Bearer test-token"


def test_immutable_upload_accepts_identical_retry_and_rejects_conflict() -> None:
    blob = FakeBlob(existing=b"same", conflict=True)
    upload_immutable(FakeBucket(blob), "object", b"same")
    with pytest.raises(RuntimeError, match="GROUNDING_IMMUTABLE_OBJECT_CONFLICT"):
        upload_immutable(
            FakeBucket(FakeBlob(existing=b"different", conflict=True)),
            "object",
            b"same",
        )


def test_approved_ingestion_retry_is_reused_only_for_matching_sources() -> None:
    ingestion_id = "ingest"
    periods = {"store": "20261"}
    digests = {"store": "abc"}
    bucket = FakeLookupBucket(
        {
            f"approvals/{ingestion_id}.json": {
                "ingestion_id": ingestion_id,
                "status": "APPROVED",
            },
            f"manifests/{ingestion_id}.json": {
                "ingestion_id": ingestion_id,
                "source_periods": periods,
                "source_digests": digests,
            },
        }
    )

    assert approved_ingestion_exists(
        bucket,
        ingestion_id=ingestion_id,
        periods=periods,
        source_digests=digests,
    )
    with pytest.raises(RuntimeError, match="GROUNDING_APPROVED_SOURCE_CONFLICT"):
        approved_ingestion_exists(
            bucket,
            ingestion_id=ingestion_id,
            periods={"store": "20262"},
            source_digests=digests,
        )


class FakeBlob:
    def __init__(self, *, existing: bytes, conflict: bool) -> None:
        self._existing = existing
        self._conflict = conflict

    def upload_from_string(self, _: bytes, *, if_generation_match: int) -> None:
        assert if_generation_match == 0
        if self._conflict:
            error = RuntimeError("precondition")
            error.code = 412  # type: ignore[attr-defined]
            raise error

    def download_as_bytes(self) -> bytes:
        return self._existing


class FakeBucket:
    def __init__(self, blob: FakeBlob) -> None:
        self._blob = blob

    def blob(self, _: str) -> FakeBlob:
        return self._blob


class FakeLookupBlob:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload

    def exists(self) -> bool:
        return self._payload is not None

    def download_as_bytes(self) -> bytes:
        assert self._payload is not None
        return json.dumps(self._payload).encode()


class FakeLookupBucket:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads

    def blob(self, object_name: str) -> FakeLookupBlob:
        return FakeLookupBlob(self._payloads.get(object_name))


def _put_cp949(target: bytearray, start: int, end: int, value: str) -> None:
    encoded = value.encode("cp949")
    assert len(encoded) <= end - start
    target[start : start + len(encoded)] = encoded


def _base_row(code: str, name: str) -> dict[str, Any]:
    return {
        "STDR_YYQU_CD": "20261",
        "ADSTRD_CD": "11440690",
        "ADSTRD_CD_NM": "망원제1동",
        "SVC_INDUTY_CD": code,
        "SVC_INDUTY_CD_NM": name,
    }


def _store_row(code: str, name: str) -> dict[str, Any]:
    return {
        **_base_row(code, name),
        "STOR_CO": 10,
        "FRC_STOR_CO": 3,
        "OPBIZ_RT": 4,
        "OPBIZ_STOR_CO": 1,
        "CLSBIZ_RT": 8,
        "CLSBIZ_STOR_CO": 2,
    }


def _population_row(period: str, prefix: str, total_field: str, total: int) -> dict[str, Any]:
    return {
        "STDR_YYQU_CD": period,
        "ADSTRD_CD": "11440690",
        "ADSTRD_CD_NM": "망원제1동",
        total_field: total,
        f"AGRDE_10_{prefix}_CO": 10,
        f"AGRDE_20_{prefix}_CO": 20,
        f"AGRDE_30_{prefix}_CO": 30,
        f"AGRDE_40_{prefix}_CO": 40,
        f"AGRDE_50_{prefix}_CO": 50,
        f"AGRDE_60_ABOVE_{prefix}_CO": 60,
    }
