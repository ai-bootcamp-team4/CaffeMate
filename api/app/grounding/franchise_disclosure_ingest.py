"""Ingest FTC franchise identity and startup-cost facts without eligibility inference."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, cast

import httpx
from google.cloud import storage  # type: ignore[attr-defined]

from app.grounding.bigquery import BigQueryLoad, BigQueryRestClient
from app.grounding.seoul_ingest import canonical_json, sha256, upload_immutable

FTC_API_BASE: Final = "https://apis.data.go.kr/1130000"
BRAND_LIST_PATH: Final = "/FftcBrandRlsInfo2_Service/getBrandinfo"
STARTUP_COST_PATH: Final = "/FftcBrandFntnStatsService/getBrandFntnStats"
BRAND_SOURCE_REF: Final = "https://www.data.go.kr/data/15125467/openapi.do"
STARTUP_COST_SOURCE_REF: Final = "https://www.data.go.kr/data/15110265/openapi.do"
PAGE_SIZE: Final = 1000

_COST_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("FRANCHISE_FEE", "jngBzmnJngAmt"),
    ("EDUCATION_FEE", "jngBzmnEduAmt"),
    ("FRANCHISEE_DEPOSIT", "jngBzmnAssrncAmt"),
    ("OTHER_INITIAL_FEE", "jngBzmnEtcAmt"),
    ("FRANCHISE_INITIAL_FEE_TOTAL", "smtnAmt"),
)

TABLE_SCHEMAS: Final[dict[str, tuple[dict[str, str], ...]]] = {
    "franchise_brand_registry": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "reporting_year", "type": "INT64", "mode": "REQUIRED"},
        {"name": "brand_management_no", "type": "STRING", "mode": "REQUIRED"},
        {"name": "headquarters_management_no", "type": "STRING", "mode": "REQUIRED"},
        {"name": "brand_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "corporation_name", "type": "STRING", "mode": "NULLABLE"},
        {"name": "business_registration_no", "type": "STRING", "mode": "NULLABLE"},
        {"name": "corporate_registration_no", "type": "STRING", "mode": "NULLABLE"},
        {"name": "business_start_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "industry_major", "type": "STRING", "mode": "REQUIRED"},
        {"name": "industry_middle", "type": "STRING", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "franchise_disclosure_fact": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "reporting_year", "type": "INT64", "mode": "REQUIRED"},
        {"name": "brand_management_no", "type": "STRING", "mode": "REQUIRED"},
        {"name": "headquarters_management_no", "type": "STRING", "mode": "REQUIRED"},
        {"name": "brand_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "corporation_name", "type": "STRING", "mode": "NULLABLE"},
        {"name": "field", "type": "STRING", "mode": "REQUIRED"},
        {"name": "value_krw", "type": "INT64", "mode": "REQUIRED"},
        {"name": "unit", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_field", "type": "STRING", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "franchise_disclosure_manifest": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "reporting_year", "type": "INT64", "mode": "REQUIRED"},
        {"name": "source_digests_json", "type": "STRING", "mode": "REQUIRED"},
        {"name": "brand_row_count", "type": "INT64", "mode": "REQUIRED"},
        {"name": "fact_row_count", "type": "INT64", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
}


class FtcFranchiseOpenApiClient:
    def __init__(
        self,
        *,
        service_key: str,
        base_url: str = FTC_API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not service_key:
            raise ValueError("FTC public-data service key is required")
        self._service_key = service_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def fetch_brand_rows(self, reporting_year: int) -> list[dict[str, Any]]:
        return self._fetch_all(
            BRAND_LIST_PATH,
            {"jngBizCrtraYr": str(reporting_year)},
        )

    def fetch_startup_cost_rows(self, reporting_year: int) -> list[dict[str, Any]]:
        return self._fetch_all(
            STARTUP_COST_PATH,
            {"yr": str(reporting_year)},
        )

    def _fetch_all(self, path: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page = 1
        total: int | None = None
        while total is None or len(rows) < total:
            try:
                with httpx.Client(
                    base_url=self._base_url,
                    timeout=30,
                    transport=self._transport,
                    headers={"User-Agent": "CaffeMate-grounding/1.0"},
                ) as client:
                    response = client.get(
                        path,
                        params={
                            "serviceKey": self._service_key,
                            "pageNo": str(page),
                            "numOfRows": str(PAGE_SIZE),
                            "resultType": "json",
                            **filters,
                        },
                    )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, json.JSONDecodeError):
                # Never surface request URLs because they contain the service key.
                raise RuntimeError("FTC_PUBLIC_DATA_REQUEST_FAILED") from None
            if not isinstance(payload, dict):
                raise RuntimeError("FTC_PUBLIC_DATA_RESPONSE_INVALID")
            if str(payload.get("resultCode", "")) not in {"00", "0"}:
                raise RuntimeError("FTC_PUBLIC_DATA_RESPONSE_ERROR")
            if total is None:
                total = int(payload.get("totalCount", 0))
            page_rows = _response_items(payload.get("items"))
            rows.extend(page_rows)
            if not page_rows:
                break
            page += 1
        if total is not None and len(rows) != total:
            raise RuntimeError("FTC_PUBLIC_DATA_ROW_COUNT_MISMATCH")
        return rows


def _response_items(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get("item")
        if isinstance(nested, list):
            return [cast(dict[str, Any], item) for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [cast(dict[str, Any], nested)]
    return []


def normalize_franchise_disclosure(
    *,
    brand_rows: list[dict[str, Any]],
    startup_cost_rows: list[dict[str, Any]],
    ingestion_id: str,
    loaded_at: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    registry: list[dict[str, object]] = []
    by_name: dict[str, list[dict[str, object]]] = {}
    for row in brand_rows:
        if row.get("indutyLclasNm") != "외식" or row.get("indutyMlsfcNm") != "커피":
            continue
        brand_name = _required_text(row, "brandNm")
        reporting_year = _year(row.get("jngBizCrtraYr"))
        normalized = {
            "ingestion_id": ingestion_id,
            "reporting_year": reporting_year,
            "brand_management_no": _required_text(row, "brandMnno"),
            "headquarters_management_no": _required_text(row, "jnghdqrtrsMnno"),
            "brand_name": brand_name,
            "corporation_name": _optional_text(row.get("corpNm")),
            "business_registration_no": _optional_text(row.get("brno")),
            "corporate_registration_no": _optional_text(row.get("crno")),
            "business_start_date": _optional_date(row.get("jngBizStrtDate")),
            "industry_major": "외식",
            "industry_middle": "커피",
            "loaded_at": loaded_at,
        }
        registry.append(normalized)
        by_name.setdefault(brand_name, []).append(normalized)
    registry.sort(key=lambda value: (str(value["brand_name"]), str(value["brand_management_no"])))

    facts: list[dict[str, object]] = []
    for row in startup_cost_rows:
        if row.get("indutyLclasNm") != "외식" or row.get("indutyMlsfcNm") != "커피":
            continue
        brand_name = _required_text(row, "brandNm")
        reporting_year = _year(row.get("yr"))
        candidates = [
            value
            for value in by_name.get(brand_name, [])
            if value["reporting_year"] == reporting_year
        ]
        corporation_name = _optional_text(row.get("corpNm"))
        if corporation_name is not None:
            exact = [value for value in candidates if value["corporation_name"] == corporation_name]
            if exact:
                candidates = exact
        if len(candidates) != 1:
            reason = "MISSING" if not candidates else "AMBIGUOUS"
            raise RuntimeError(f"FTC_BRAND_IDENTITY_{reason}:{brand_name}:{reporting_year}")
        identity = candidates[0]
        values: dict[str, int] = {}
        for field, source_field in _COST_FIELDS:
            parsed = _thousand_krw(row.get(source_field))
            if parsed is not None:
                values[field] = parsed
        components = [
            values.get("FRANCHISE_FEE"),
            values.get("EDUCATION_FEE"),
            values.get("FRANCHISEE_DEPOSIT"),
            values.get("OTHER_INITIAL_FEE"),
        ]
        total = values.get("FRANCHISE_INITIAL_FEE_TOTAL")
        if all(value is not None for value in components) and total is not None:
            if sum(cast(int, value) for value in components) != total:
                raise RuntimeError(
                    f"FTC_STARTUP_COST_TOTAL_MISMATCH:{brand_name}:{reporting_year}"
                )
        for field, source_field in _COST_FIELDS:
            amount = values.get(field)
            if amount is None:
                continue
            facts.append(
                {
                    "ingestion_id": ingestion_id,
                    "reporting_year": reporting_year,
                    "brand_management_no": identity["brand_management_no"],
                    "headquarters_management_no": identity["headquarters_management_no"],
                    "brand_name": brand_name,
                    "corporation_name": corporation_name,
                    "field": field,
                    "value_krw": amount,
                    "unit": "KRW",
                    "source_field": source_field,
                    "loaded_at": loaded_at,
                }
            )
    field_order = {field: index for index, (field, _) in enumerate(_COST_FIELDS)}
    facts.sort(
        key=lambda value: (
            str(value["brand_name"]),
            str(value["brand_management_no"]),
            field_order[str(value["field"])],
        )
    )
    return registry, facts


def _required_text(row: Mapping[str, object], field: str) -> str:
    value = _optional_text(row.get(field))
    if value is None:
        raise RuntimeError(f"FTC_REQUIRED_FIELD_MISSING:{field}")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _year(value: object) -> int:
    text = _optional_text(value)
    if text is None or len(text) != 4 or not text.isdigit():
        raise RuntimeError("FTC_REPORTING_YEAR_INVALID")
    return int(text)


def _optional_date(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    digits = text.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise RuntimeError("FTC_DATE_INVALID")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def _thousand_krw(value: object) -> int | None:
    text = _optional_text(value)
    if text is None:
        return None
    cleaned = text.replace(",", "")
    try:
        number = int(cleaned)
    except ValueError as error:
        raise RuntimeError("FTC_AMOUNT_INVALID") from error
    if number < 0:
        raise RuntimeError("FTC_AMOUNT_NEGATIVE")
    return number * 1_000


@dataclass(frozen=True)
class FranchiseDisclosureIngestionConfig:
    project_id: str
    region: str
    bucket_name: str
    dataset_id: str
    service_key: str

    @classmethod
    def from_environment(cls) -> FranchiseDisclosureIngestionConfig:
        project_id = os.getenv("CAFFEMATE_GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        bucket_name = os.getenv("CAFFEMATE_GROUNDING_BUCKET")
        service_key = os.getenv("DATA_GO_KR_SERVICE_KEY")
        if not project_id or not bucket_name or not service_key:
            raise RuntimeError("FTC_FRANCHISE_CONFIG_MISSING")
        region = os.getenv("CAFFEMATE_GCP_REGION", "asia-northeast3")
        if region != "asia-northeast3":
            raise RuntimeError("GROUNDING_REGION_INVALID")
        return cls(
            project_id=project_id,
            region=region,
            bucket_name=bucket_name,
            dataset_id=os.getenv("CAFFEMATE_GROUNDING_DATASET", "caffemate_grounding"),
            service_key=service_key,
        )


class FranchiseDisclosureIngestor:
    def __init__(
        self,
        config: FranchiseDisclosureIngestionConfig,
        *,
        storage_client: storage.Client | None = None,
        bigquery_client: BigQueryRestClient | None = None,
        ftc_client: FtcFranchiseOpenApiClient | None = None,
        today: date | None = None,
    ) -> None:
        self._config = config
        self._storage = storage_client or storage.Client(project=config.project_id)
        self._bigquery = bigquery_client or BigQueryRestClient(
            project_id=config.project_id,
            dataset_id=config.dataset_id,
            location=config.region,
        )
        self._ftc = ftc_client or FtcFranchiseOpenApiClient(service_key=config.service_key)
        self._today = today

    def run(self) -> str:
        reporting_year, brand_rows, startup_rows = self._latest_common_year()
        loaded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        raw = {"brand_registry": brand_rows, "startup_cost": startup_rows}
        source_digests = {
            name: sha256(canonical_json(rows)) for name, rows in sorted(raw.items())
        }
        ingestion_id = sha256(
            canonical_json({"reporting_year": reporting_year, "sources": source_digests})
        )[:24]
        brands, facts = normalize_franchise_disclosure(
            brand_rows=brand_rows,
            startup_cost_rows=startup_rows,
            ingestion_id=ingestion_id,
            loaded_at=loaded_at,
        )
        if not brands or not facts:
            raise RuntimeError("FTC_FRANCHISE_NORMALIZED_DATA_EMPTY")
        bucket = self._storage.bucket(self._config.bucket_name)
        for name, rows in raw.items():
            upload_immutable(
                bucket,
                f"franchise-disclosure/raw/{ingestion_id}/{name}.json",
                canonical_json(rows),
            )
        self._load_rows(bucket, ingestion_id, "franchise_brand_registry", brands)
        self._load_rows(bucket, ingestion_id, "franchise_disclosure_fact", facts)
        manifest = {
            "ingestion_id": ingestion_id,
            "status": "APPROVED",
            "reporting_year": reporting_year,
            "source_digests_json": canonical_json(source_digests).decode(),
            "brand_row_count": len(brands),
            "fact_row_count": len(facts),
            "loaded_at": loaded_at,
        }
        self._load_rows(
            bucket,
            ingestion_id,
            "franchise_disclosure_manifest",
            [manifest],
        )
        upload_immutable(
            bucket,
            f"franchise-disclosure/approvals/{ingestion_id}.json",
            canonical_json({"ingestion_id": ingestion_id, "status": "APPROVED"}),
        )
        return ingestion_id

    def _latest_common_year(self) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        current = (self._today or date.today()).year
        for year in range(current, current - 6, -1):
            brands = self._ftc.fetch_brand_rows(year)
            costs = self._ftc.fetch_startup_cost_rows(year)
            if brands and costs:
                return year, brands, costs
        raise RuntimeError("FTC_COMMON_REPORTING_YEAR_NOT_FOUND")

    def _load_rows(
        self,
        bucket: Any,
        ingestion_id: str,
        table: str,
        rows: list[dict[str, object]],
    ) -> None:
        path = f"franchise-disclosure/normalized/{ingestion_id}/{table}.ndjson"
        upload_immutable(bucket, path, b"".join(canonical_json(row) + b"\n" for row in rows))
        self._bigquery.load_ndjson(
            load=BigQueryLoad(
                table=table,
                source_uri=f"gs://{self._config.bucket_name}/{path}",
                schema=TABLE_SCHEMAS[table],
            ),
            job_id=f"caffemate_ftc_{table}_{ingestion_id}",
        )


def main() -> None:
    FranchiseDisclosureIngestor(FranchiseDisclosureIngestionConfig.from_environment()).run()


if __name__ == "__main__":
    main()