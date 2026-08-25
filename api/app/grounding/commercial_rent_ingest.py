"""Ingest official REB commercial-rent reference rows for deterministic finance."""

from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, cast

import httpx
from google.cloud import storage  # type: ignore[attr-defined]

from app.candidates.seed_registry import CommercialPropertyClass
from app.grounding.bigquery import BigQueryLoad, BigQueryRestClient
from app.grounding.seoul_ingest import canonical_json, sha256, upload_immutable

REB_BASE_URL: Final = "https://www.reb.or.kr/r-one"
TABLE_ID_PATTERN: Final = re.compile(r"^T[A-Z]?\d+$")
QUARTER_COLUMN_PATTERN: Final = re.compile(r"^COL_(\d{4})(0[1-4])\d+OD$")

REGION_CODES: Final[dict[str, str]] = {
    "서울": "11",
    "부산": "26",
    "대구": "27",
    "인천": "28",
    "광주": "29",
    "대전": "30",
    "울산": "31",
    "세종": "36",
    "경기": "41",
    "충북": "43",
    "충남": "44",
    "전남": "46",
    "경북": "47",
    "경남": "48",
    "제주": "50",
    "강원": "51",
    "전북": "52",
}


@dataclass(frozen=True)
class RebTablePair:
    rent_table_id: str
    conversion_table_id: str


REB_TABLES: Final[dict[CommercialPropertyClass, RebTablePair]] = {
    CommercialPropertyClass.SMALL_RETAIL: RebTablePair(
        rent_table_id="T248223134698125",
        conversion_table_id="T246253134905233",
    ),
    CommercialPropertyClass.MEDIUM_LARGE_RETAIL: RebTablePair(
        rent_table_id="T244363134858603",
        conversion_table_id="T241883134877452",
    ),
}

TABLE_SCHEMAS: Final[dict[str, tuple[dict[str, str], ...]]] = {
    "commercial_rent_reference": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "period_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "region_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "region_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "property_class", "type": "STRING", "mode": "REQUIRED"},
        {
            "name": "effective_rent_krw_per_sqm_month",
            "type": "INT64",
            "mode": "REQUIRED",
        },
        {"name": "conversion_rate_bps", "type": "INT64", "mode": "REQUIRED"},
        {"name": "coverage_status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "floor_basis", "type": "STRING", "mode": "REQUIRED"},
        {"name": "rent_table_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "conversion_table_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "commercial_rent_manifest": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "period_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_digests_json", "type": "STRING", "mode": "REQUIRED"},
        {"name": "row_count", "type": "INT64", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
}


@dataclass(frozen=True)
class RebTableSnapshot:
    table_id: str
    page_html: str
    preview_json: str

    @property
    def preview(self) -> dict[str, object]:
        value = json.loads(self.preview_json)
        if not isinstance(value, dict):
            raise RuntimeError("REB_PREVIEW_INVALID")
        return cast(dict[str, object], value)


class RebEasyStatClient:
    """Read the same official R-ONE statistics rendered by the public table UI."""

    def __init__(
        self,
        *,
        base_url: str = REB_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def fetch_table(self, table_id: str) -> RebTableSnapshot:
        if not TABLE_ID_PATTERN.fullmatch(table_id):
            raise ValueError("REB_TABLE_ID_INVALID")
        with httpx.Client(
            base_url=self._base_url,
            timeout=30,
            transport=self._transport,
            follow_redirects=True,
            headers={"User-Agent": "CaffeMate-grounding/1.0"},
        ) as client:
            page = client.get(f"/portal/stat/easyStatPage/{table_id}.do")
            page.raise_for_status()
            parameter = _first_query_parameter(page.text)
            preview = client.post(
                "/portal/stat/sttsDataPreviewList.do",
                content=f"{parameter}&searchType=",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            preview.raise_for_status()
        payload = preview.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("DATA"), list):
            raise RuntimeError("REB_PREVIEW_INVALID")
        return RebTableSnapshot(
            table_id=table_id,
            page_html=page.text,
            preview_json=json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )


def _first_query_parameter(page_html: str) -> str:
    match = re.search(
        r'<input[^>]+id=["\']firParam["\'][^>]+value=["\']([^"\']+)["\']',
        page_html,
        flags=re.IGNORECASE,
    )
    if match is None:
        # Some revisions render the attributes in the opposite order.
        match = re.search(
            r'<input[^>]+value=["\']([^"\']+)["\'][^>]+id=["\']firParam["\']',
            page_html,
            flags=re.IGNORECASE,
        )
    if match is None:
        raise RuntimeError("REB_FIR_PARAM_MISSING")
    value = html.unescape(match.group(1))
    if "statblId=" not in value or "dtacycleCd=QY" not in value:
        raise RuntimeError("REB_FIR_PARAM_INVALID")
    return value


def normalize_property_reference(
    *,
    property_class: CommercialPropertyClass,
    rent_table_id: str,
    conversion_table_id: str,
    rent_preview: Mapping[str, object],
    conversion_preview: Mapping[str, object],
    ingestion_id: str,
    loaded_at: str,
) -> list[dict[str, object]]:
    rent_rows = _rows(rent_preview)
    conversion_rows = _rows(conversion_preview)
    period = _latest_common_quarter(rent_rows, conversion_rows)
    rent_by_region = _province_values(rent_rows, period=period)
    conversion_by_region = _province_values(conversion_rows, period=period)
    names = sorted(
        set(rent_by_region) & set(conversion_by_region),
        key=lambda value: REGION_CODES.get(value, "99"),
    )
    normalized: list[dict[str, object]] = []
    for name in names:
        code = REGION_CODES.get(name)
        if code is None:
            continue
        rent = rent_by_region[name]
        conversion = conversion_by_region[name]
        normalized.append(
            {
                "ingestion_id": ingestion_id,
                "period_code": period,
                "region_code": code,
                "region_name": name,
                "property_class": property_class.value,
                "effective_rent_krw_per_sqm_month": round(rent * 1_000),
                "conversion_rate_bps": round(conversion * 100),
                "coverage_status": "PARENT_REGION",
                "floor_basis": "FIRST_FLOOR",
                "rent_table_id": rent_table_id,
                "conversion_table_id": conversion_table_id,
                "loaded_at": loaded_at,
            }
        )
    return normalized


def _rows(preview: Mapping[str, object]) -> list[dict[str, object]]:
    value = preview.get("DATA")
    if not isinstance(value, list):
        raise RuntimeError("REB_PREVIEW_ROWS_INVALID")
    rows = [cast(dict[str, object], row) for row in value if isinstance(row, dict)]
    if not rows:
        raise RuntimeError("REB_PREVIEW_ROWS_EMPTY")
    return rows


def _quarters(rows: list[dict[str, object]]) -> set[str]:
    values: set[str] = set()
    for row in rows:
        for key in row:
            match = QUARTER_COLUMN_PATTERN.fullmatch(key)
            if match is not None:
                values.add(f"{match.group(1)}Q{int(match.group(2))}")
    return values


def _latest_common_quarter(
    rent_rows: list[dict[str, object]],
    conversion_rows: list[dict[str, object]],
) -> str:
    common = sorted(_quarters(rent_rows) & _quarters(conversion_rows))
    if not common:
        raise RuntimeError("REB_COMMON_QUARTER_MISSING")
    return common[-1]


def _province_values(
    rows: list[dict[str, object]],
    *,
    period: str,
) -> dict[str, float]:
    year, quarter = period.split("Q", maxsplit=1)
    prefix = f"COL_{year}{int(quarter):02d}"
    values: dict[str, float] = {}
    for row in rows:
        first = row.get("CATE1")
        second = row.get("CATE2")
        third = row.get("CATE3")
        if not isinstance(first, str) or first not in REGION_CODES:
            continue
        if first != second or first != third:
            continue
        columns = [key for key in row if key.startswith(prefix) and key.endswith("OD")]
        if len(columns) != 1:
            raise RuntimeError(f"REB_PERIOD_COLUMN_AMBIGUOUS:{first}:{period}")
        raw = row.get(columns[0])
        try:
            number = float(cast(str | int | float, raw))
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"REB_VALUE_INVALID:{first}:{period}") from error
        if number < 0:
            raise RuntimeError(f"REB_VALUE_NEGATIVE:{first}:{period}")
        values[first] = number
    return values


@dataclass(frozen=True)
class CommercialRentIngestionConfig:
    project_id: str
    region: str
    bucket_name: str
    dataset_id: str

    @classmethod
    def from_environment(cls) -> CommercialRentIngestionConfig:
        project_id = os.getenv("CAFFEMATE_GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        bucket_name = os.getenv("CAFFEMATE_GROUNDING_BUCKET")
        if not project_id or not bucket_name:
            raise RuntimeError("COMMERCIAL_RENT_CONFIG_MISSING")
        region = os.getenv("CAFFEMATE_GCP_REGION", "asia-northeast3")
        if region != "asia-northeast3":
            raise RuntimeError("GROUNDING_REGION_INVALID")
        return cls(
            project_id=project_id,
            region=region,
            bucket_name=bucket_name,
            dataset_id=os.getenv("CAFFEMATE_GROUNDING_DATASET", "caffemate_grounding"),
        )


class CommercialRentIngestor:
    def __init__(
        self,
        config: CommercialRentIngestionConfig,
        *,
        storage_client: storage.Client | None = None,
        bigquery_client: BigQueryRestClient | None = None,
        reb_client: RebEasyStatClient | None = None,
    ) -> None:
        self._config = config
        self._storage = storage_client or storage.Client(project=config.project_id)
        self._bigquery = bigquery_client or BigQueryRestClient(
            project_id=config.project_id,
            dataset_id=config.dataset_id,
            location=config.region,
        )
        self._reb = reb_client or RebEasyStatClient()

    def run(self) -> str:
        loaded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        snapshots: dict[str, RebTableSnapshot] = {}
        for pair in REB_TABLES.values():
            for table_id in (pair.rent_table_id, pair.conversion_table_id):
                snapshots.setdefault(table_id, self._reb.fetch_table(table_id))
        digests = {
            table_id: sha256(snapshot.preview_json.encode())
            for table_id, snapshot in sorted(snapshots.items())
        }
        ingestion_id = sha256(canonical_json({"tables": sorted(digests.items())}))[:24]
        rows: list[dict[str, object]] = []
        for property_class, pair in REB_TABLES.items():
            class_rows = normalize_property_reference(
                property_class=property_class,
                rent_table_id=pair.rent_table_id,
                conversion_table_id=pair.conversion_table_id,
                rent_preview=snapshots[pair.rent_table_id].preview,
                conversion_preview=snapshots[pair.conversion_table_id].preview,
                ingestion_id=ingestion_id,
                loaded_at=loaded_at,
            )
            if len(class_rows) != len(REGION_CODES):
                raise RuntimeError(
                    f"REB_PROVINCE_COVERAGE_INCOMPLETE:{property_class.value}"
                )
            rows.extend(class_rows)
        periods = {cast(str, row["period_code"]) for row in rows}
        if len(periods) != 1:
            raise RuntimeError("REB_PROPERTY_CLASS_PERIOD_MISMATCH")
        period = next(iter(periods))
        bucket = self._storage.bucket(self._config.bucket_name)
        for table_id, snapshot in snapshots.items():
            upload_immutable(
                bucket,
                f"commercial-rent/raw/{ingestion_id}/{table_id}.html",
                snapshot.page_html.encode(),
            )
            upload_immutable(
                bucket,
                f"commercial-rent/raw/{ingestion_id}/{table_id}.json",
                snapshot.preview_json.encode(),
            )
        normalized_path = f"commercial-rent/normalized/{ingestion_id}/reference.ndjson"
        normalized_payload = b"".join(canonical_json(row) + b"\n" for row in rows)
        upload_immutable(bucket, normalized_path, normalized_payload)
        self._bigquery.load_ndjson(
            load=BigQueryLoad(
                table="commercial_rent_reference",
                source_uri=f"gs://{self._config.bucket_name}/{normalized_path}",
                schema=TABLE_SCHEMAS["commercial_rent_reference"],
            ),
            job_id=f"caffemate_commercial_rent_{ingestion_id}",
        )
        manifest = {
            "ingestion_id": ingestion_id,
            "status": "APPROVED",
            "period_code": period,
            "source_digests_json": canonical_json(digests).decode(),
            "row_count": len(rows),
            "loaded_at": loaded_at,
        }
        manifest_path = f"commercial-rent/normalized/{ingestion_id}/manifest.ndjson"
        upload_immutable(bucket, manifest_path, canonical_json(manifest) + b"\n")
        self._bigquery.load_ndjson(
            load=BigQueryLoad(
                table="commercial_rent_manifest",
                source_uri=f"gs://{self._config.bucket_name}/{manifest_path}",
                schema=TABLE_SCHEMAS["commercial_rent_manifest"],
            ),
            job_id=f"caffemate_commercial_rent_manifest_{ingestion_id}",
        )
        upload_immutable(
            bucket,
            f"commercial-rent/approvals/{ingestion_id}.json",
            canonical_json({"ingestion_id": ingestion_id, "status": "APPROVED"}),
        )
        print(
            json.dumps(
                {
                    "status": "APPROVED",
                    "ingestion_id": ingestion_id,
                    "period_code": period,
                    "rows": len(rows),
                }
            )
        )
        return ingestion_id


def main() -> None:
    CommercialRentIngestor(CommercialRentIngestionConfig.from_environment()).run()


if __name__ == "__main__":
    main()