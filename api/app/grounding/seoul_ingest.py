from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final, cast

import httpx
from google.cloud import storage  # type: ignore[attr-defined]

from app.grounding.bigquery import BigQueryLoad, BigQueryRestClient

SEOUL_API_BASE: Final = "http://openapi.seoul.go.kr:8088"
MOIS_MAPPING_URL: Final = (
    "https://www.mois.go.kr/cmm/fms/FileDown.do?"
    "atchFileId=FILE_00146280tlU2Y2B&fileSn=0"
)
CAFE_INDUSTRY_CODE: Final = "CS100010"
PAGE_SIZE: Final = 1000

OFFICIAL_SOURCE_PAGES: Final = {
    "store": "https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
    "sales": "https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do",
    "foot": "https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do?tab=A",
    "resident": "https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do?tab=A",
    "worker": "https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do",
    "mapping": (
        "https://www.mois.go.kr/frt/bbs/type001/commonSelectBoardArticle.do?"
        "bbsId=BBSMSTR_000000000052&nttId=127039"
    ),
}

TABLE_SCHEMAS: Final[dict[str, tuple[dict[str, str], ...]]] = {
    "area_mapping": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_revision", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "legal_dong_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "legal_dong_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "created_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "deleted_date", "type": "DATE", "mode": "NULLABLE"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "seoul_cafe_store_fact": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "period_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "store_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "franchise_store_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "open_rate", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "open_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "closure_rate", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "closure_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "seoul_cafe_sales_fact": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "period_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "estimated_sales_krw", "type": "INT64", "mode": "NULLABLE"},
        {"name": "estimated_sales_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "seoul_population_fact": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "period_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_code", "type": "STRING", "mode": "REQUIRED"},
        {"name": "admin_dong_name", "type": "STRING", "mode": "REQUIRED"},
        {"name": "population_kind", "type": "STRING", "mode": "REQUIRED"},
        {"name": "total_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_10_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_20_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_30_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_40_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_50_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "age_60_plus_count", "type": "FLOAT64", "mode": "NULLABLE"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    ),
    "source_manifest": (
        {"name": "ingestion_id", "type": "STRING", "mode": "REQUIRED"},
        {"name": "status", "type": "STRING", "mode": "REQUIRED"},
        {"name": "loaded_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
        {"name": "source_periods_json", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_digests_json", "type": "STRING", "mode": "REQUIRED"},
        {"name": "source_uris_json", "type": "STRING", "mode": "REQUIRED"},
        {"name": "row_counts_json", "type": "STRING", "mode": "REQUIRED"},
    ),
}


@dataclass(frozen=True)
class IngestionConfig:
    project_id: str
    region: str
    bucket_name: str
    dataset_id: str
    seoul_api_key: str
    seoul_api_base: str = SEOUL_API_BASE
    mois_mapping_url: str = MOIS_MAPPING_URL

    @classmethod
    def from_environment(cls) -> IngestionConfig:
        values = {
            "project_id": os.getenv("CAFFEMATE_GCP_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT"),
            "bucket_name": os.getenv("CAFFEMATE_GROUNDING_BUCKET"),
            "seoul_api_key": os.getenv("SEOUL_OPEN_API_KEY"),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"GROUNDING_CONFIG_MISSING:{','.join(sorted(missing))}")
        region = os.getenv("CAFFEMATE_GCP_REGION", "asia-northeast3")
        if region != "asia-northeast3":
            raise RuntimeError("GROUNDING_REGION_INVALID")
        return cls(
            project_id=cast(str, values["project_id"]),
            region=region,
            bucket_name=cast(str, values["bucket_name"]),
            dataset_id=os.getenv("CAFFEMATE_GROUNDING_DATASET", "caffemate_grounding"),
            seoul_api_key=cast(str, values["seoul_api_key"]),
            seoul_api_base=os.getenv("SEOUL_OPEN_API_BASE", SEOUL_API_BASE),
            mois_mapping_url=os.getenv("MOIS_MAPPING_URL", MOIS_MAPPING_URL),
        )


class SeoulOpenApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = SEOUL_API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Seoul API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def latest_period(self, service: str, *, today: date | None = None) -> str:
        for period in candidate_quarters(today or date.today(), count=12):
            try:
                rows = self.fetch_rows(service, period=period, limit=1)
            except LookupError:
                continue
            if rows:
                actual = str(rows[0].get("STDR_YYQU_CD", ""))
                if actual == period:
                    return period
        raise RuntimeError(f"SEOUL_LATEST_PERIOD_NOT_FOUND:{service}")

    def fetch_rows(
        self,
        service: str,
        *,
        period: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        start = 1
        total: int | None = None
        while total is None or start <= total:
            end = start + PAGE_SIZE - 1
            if limit is not None:
                end = min(end, limit)
            path = f"/{self._api_key}/json/{service}/{start}/{end}"
            if period:
                path = f"{path}/{period}"
            payload = self._get_json(path)
            envelope = payload.get(service)
            if not isinstance(envelope, dict):
                message = payload.get("RESULT", {})
                if isinstance(message, dict) and message.get("CODE") == "INFO-200":
                    raise LookupError("SEOUL_DATA_NOT_FOUND")
                raise RuntimeError(f"SEOUL_ENVELOPE_INVALID:{service}")
            if total is None:
                total = int(envelope.get("list_total_count", 0))
            page = envelope.get("row", [])
            if not isinstance(page, list):
                raise RuntimeError(f"SEOUL_ROWS_INVALID:{service}")
            collected.extend(cast(list[dict[str, Any]], page))
            if limit is not None and len(collected) >= limit:
                return collected[:limit]
            if not page:
                break
            start += len(page)
        if total is not None and len(collected) != total:
            raise RuntimeError(f"SEOUL_ROW_COUNT_MISMATCH:{service}")
        return collected

    def _get_json(self, path: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(
                    base_url=self._base_url,
                    timeout=30,
                    transport=self._transport,
                ) as client:
                    response = client.get(path)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("SEOUL_RESPONSE_INVALID")
                return cast(dict[str, Any], payload)
            except (httpx.HTTPError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        if last_error is None:
            raise RuntimeError("SEOUL_REQUEST_FAILED")
        # Do not chain the HTTP exception: its request URL contains the API key.
        raise RuntimeError("SEOUL_REQUEST_FAILED") from None


class GroundingIngestor:
    def __init__(
        self,
        config: IngestionConfig,
        *,
        storage_client: storage.Client | None = None,
        bigquery_client: BigQueryRestClient | None = None,
        seoul_client: SeoulOpenApiClient | None = None,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._storage = storage_client or storage.Client(project=config.project_id)
        self._bigquery = bigquery_client or BigQueryRestClient(
            project_id=config.project_id,
            dataset_id=config.dataset_id,
            location=config.region,
        )
        self._seoul = seoul_client or SeoulOpenApiClient(
            api_key=config.seoul_api_key,
            base_url=config.seoul_api_base,
        )
        self._http_transport = http_transport

    def run(self) -> str:
        loaded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        periods = {
            "store": self._seoul.latest_period("VwsmAdstrdStorW"),
            "sales": self._seoul.latest_period("VwsmAdstrdSelngW"),
            "worker": self._seoul.latest_period("VwsmAdstrdWrcPopltnW"),
        }
        raw: dict[str, bytes] = {
            "store": encode_ndjson(
                self._seoul.fetch_rows("VwsmAdstrdStorW", period=periods["store"])
            ),
            "sales": encode_ndjson(
                self._seoul.fetch_rows("VwsmAdstrdSelngW", period=periods["sales"])
            ),
            "foot": encode_ndjson(self._seoul.fetch_rows("VwsmAdstrdFlpopW")),
            "resident": encode_ndjson(self._seoul.fetch_rows("VwsmAdstrdRepopW")),
            "worker": encode_ndjson(
                self._seoul.fetch_rows("VwsmAdstrdWrcPopltnW", period=periods["worker"])
            ),
            "mapping_zip": self._download_mapping(),
        }
        raw_rows = {
            name: decode_ndjson(payload)
            for name, payload in raw.items()
            if name != "mapping_zip"
        }
        periods["foot"] = latest_period(raw_rows["foot"])
        periods["resident"] = latest_period(raw_rows["resident"])
        source_digests = {name: sha256(payload) for name, payload in sorted(raw.items())}
        ingestion_id = sha256(canonical_json({"periods": periods, "digests": source_digests}))[:24]

        mapping_revision, mapping_rows = parse_mois_mapping(raw["mapping_zip"])
        normalized = {
            "area_mapping": normalize_mapping(
                mapping_rows,
                ingestion_id=ingestion_id,
                source_revision=mapping_revision,
                loaded_at=loaded_at,
            ),
            "seoul_cafe_store_fact": normalize_cafe_store(
                raw_rows["store"],
                ingestion_id=ingestion_id,
                loaded_at=loaded_at,
            ),
            "seoul_cafe_sales_fact": normalize_cafe_sales(
                raw_rows["sales"],
                ingestion_id=ingestion_id,
                loaded_at=loaded_at,
            ),
            "seoul_population_fact": normalize_populations(
                {
                    "FOOT_TRAFFIC": latest_period_rows(
                        raw_rows["foot"]
                    ),
                    "RESIDENT": latest_period_rows(
                        raw_rows["resident"]
                    ),
                    "WORKER": raw_rows["worker"],
                },
                ingestion_id=ingestion_id,
                loaded_at=loaded_at,
            ),
        }
        validate_normalized(normalized, periods=periods)

        bucket = self._storage.bucket(self._config.bucket_name)
        source_uris: dict[str, str] = {}
        for name, payload in raw.items():
            suffix = "zip" if name == "mapping_zip" else "ndjson"
            object_name = f"raw/{ingestion_id}/{name}.{suffix}"
            upload_immutable(bucket, object_name, payload)
            source_uris[name] = f"gs://{self._config.bucket_name}/{object_name}"

        normalized_uris: dict[str, str] = {}
        row_counts: dict[str, int] = {}
        for table, rows in normalized.items():
            payload = encode_ndjson(rows)
            object_name = f"normalized/{ingestion_id}/{table}.ndjson"
            upload_immutable(bucket, object_name, payload)
            normalized_uris[table] = f"gs://{self._config.bucket_name}/{object_name}"
            row_counts[table] = len(rows)

        manifest = {
            "schema_version": "1.0.0",
            "ingestion_id": ingestion_id,
            "loaded_at": loaded_at,
            "source_periods": periods,
            "source_digests": source_digests,
            "source_uris": source_uris,
            "normalized_uris": normalized_uris,
            "row_counts": row_counts,
            "official_source_pages": OFFICIAL_SOURCE_PAGES,
            "mapping_revision": mapping_revision,
        }
        upload_immutable(
            bucket,
            f"manifests/{ingestion_id}.json",
            canonical_json(manifest),
        )

        for table, uri in normalized_uris.items():
            self._bigquery.load_ndjson(
                load=BigQueryLoad(table=table, source_uri=uri, schema=TABLE_SCHEMAS[table]),
                job_id=f"caffemate_{table}_{ingestion_id}",
            )

        manifest_row = {
            "ingestion_id": ingestion_id,
            "status": "APPROVED",
            "loaded_at": loaded_at,
            "source_periods_json": canonical_json(periods).decode(),
            "source_digests_json": canonical_json(source_digests).decode(),
            "source_uris_json": canonical_json(source_uris).decode(),
            "row_counts_json": canonical_json(row_counts).decode(),
        }
        manifest_object = f"normalized/{ingestion_id}/source_manifest.ndjson"
        upload_immutable(bucket, manifest_object, encode_ndjson([manifest_row]))
        self._bigquery.load_ndjson(
            load=BigQueryLoad(
                table="source_manifest",
                source_uri=f"gs://{self._config.bucket_name}/{manifest_object}",
                schema=TABLE_SCHEMAS["source_manifest"],
            ),
            job_id=f"caffemate_source_manifest_{ingestion_id}",
        )
        upload_immutable(
            bucket,
            f"approvals/{ingestion_id}.json",
            canonical_json({"ingestion_id": ingestion_id, "status": "APPROVED"}),
        )
        print(json.dumps({"status": "APPROVED", "ingestion_id": ingestion_id, "rows": row_counts}))
        return ingestion_id

    def _download_mapping(self) -> bytes:
        with httpx.Client(
            timeout=45,
            transport=self._http_transport,
            follow_redirects=True,
        ) as client:
            response = client.get(self._config.mois_mapping_url)
        response.raise_for_status()
        if not response.content.startswith(b"PK"):
            raise RuntimeError("MOIS_MAPPING_ARCHIVE_INVALID")
        return response.content


def candidate_quarters(today: date, *, count: int) -> list[str]:
    quarter = ((today.month - 1) // 3) + 1
    year = today.year
    result: list[str] = []
    for _ in range(count):
        result.append(f"{year}{quarter}")
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return result


def parse_mois_mapping(payload: bytes) -> tuple[str, list[dict[str, str | None]]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if "KIKmix" in name and not name.lower().endswith("xlsx")
        ]
        if len(candidates) != 1:
            raise RuntimeError("MOIS_MAPPING_MEMBER_AMBIGUOUS")
        member = candidates[0]
        raw = archive.read(member)
    rows: list[dict[str, str | None]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        admin_code = decode_slice(line, 0, 10)
        legal_code = decode_slice(line, 104, 114)
        if not admin_code.isdigit() or not legal_code.isdigit():
            continue
        rows.append(
            {
                "admin_dong_code": admin_code,
                "admin_dong_name": decode_slice(line, 73, 104),
                "legal_dong_code": legal_code,
                "legal_dong_name": decode_slice(line, 115, 146),
                "created_date": compact_date(decode_slice(line, 146, 154)),
                "deleted_date": compact_date(decode_slice(line, 155, 163)),
            }
        )
    if not rows:
        raise RuntimeError("MOIS_MAPPING_EMPTY")
    revision = "unknown"
    digits = "".join(character for character in member if character.isdigit())
    if len(digits) >= 8:
        revision = digits[-8:]
    return revision, rows


def decode_slice(line: bytes, start: int, end: int) -> str:
    return line[start:end].decode("cp949").strip()


def compact_date(value: str) -> str | None:
    if not value:
        return None
    if len(value) != 8 or not value.isdigit():
        raise RuntimeError("MOIS_MAPPING_DATE_INVALID")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def normalize_mapping(
    rows: Sequence[Mapping[str, str | None]],
    *,
    ingestion_id: str,
    source_revision: str,
    loaded_at: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        admin_code = cast(str, row["admin_dong_code"])
        normalized.append(
            {
                "ingestion_id": ingestion_id,
                "source_revision": source_revision,
                "admin_dong_code": admin_code[:8],
                "admin_dong_name": row["admin_dong_name"],
                "legal_dong_code": row["legal_dong_code"],
                "legal_dong_name": row["legal_dong_name"],
                "created_date": row["created_date"],
                "deleted_date": row["deleted_date"],
                "loaded_at": loaded_at,
            }
        )
    return normalized


def normalize_cafe_store(
    rows: Sequence[Mapping[str, Any]], *, ingestion_id: str, loaded_at: str
) -> list[dict[str, Any]]:
    return [
        {
            "ingestion_id": ingestion_id,
            "period_code": str(row["STDR_YYQU_CD"]),
            "admin_dong_code": str(row["ADSTRD_CD"]),
            "admin_dong_name": str(row["ADSTRD_CD_NM"]),
            "store_count": number(row.get("STOR_CO")),
            "franchise_store_count": number(row.get("FRC_STOR_CO")),
            "open_rate": number(row.get("OPBIZ_RT")),
            "open_count": number(row.get("OPBIZ_STOR_CO")),
            "closure_rate": number(row.get("CLSBIZ_RT")),
            "closure_count": number(row.get("CLSBIZ_STOR_CO")),
            "loaded_at": loaded_at,
        }
        for row in rows
        if row.get("SVC_INDUTY_CD") == CAFE_INDUSTRY_CODE
    ]


def normalize_cafe_sales(
    rows: Sequence[Mapping[str, Any]], *, ingestion_id: str, loaded_at: str
) -> list[dict[str, Any]]:
    return [
        {
            "ingestion_id": ingestion_id,
            "period_code": str(row["STDR_YYQU_CD"]),
            "admin_dong_code": str(row["ADSTRD_CD"]),
            "admin_dong_name": str(row["ADSTRD_CD_NM"]),
            "estimated_sales_krw": integer(row.get("THSMON_SELNG_AMT")),
            "estimated_sales_count": number(row.get("THSMON_SELNG_CO")),
            "loaded_at": loaded_at,
        }
        for row in rows
        if row.get("SVC_INDUTY_CD") == CAFE_INDUSTRY_CODE
    ]


def normalize_populations(
    sources: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    ingestion_id: str,
    loaded_at: str,
) -> list[dict[str, Any]]:
    prefixes = {
        "FOOT_TRAFFIC": ("FLPOP", "TOT_FLPOP_CO"),
        "RESIDENT": ("REPOP", "TOT_REPOP_CO"),
        "WORKER": ("WRC_POPLTN", "TOT_WRC_POPLTN_CO"),
    }
    normalized: list[dict[str, Any]] = []
    for kind, rows in sources.items():
        prefix, total_field = prefixes[kind]
        for row in rows:
            normalized.append(
                {
                    "ingestion_id": ingestion_id,
                    "period_code": str(row["STDR_YYQU_CD"]),
                    "admin_dong_code": str(row["ADSTRD_CD"]),
                    "admin_dong_name": str(row["ADSTRD_CD_NM"]),
                    "population_kind": kind,
                    "total_count": number(row.get(total_field)),
                    "age_10_count": number(row.get(f"AGRDE_10_{prefix}_CO")),
                    "age_20_count": number(row.get(f"AGRDE_20_{prefix}_CO")),
                    "age_30_count": number(row.get(f"AGRDE_30_{prefix}_CO")),
                    "age_40_count": number(row.get(f"AGRDE_40_{prefix}_CO")),
                    "age_50_count": number(row.get(f"AGRDE_50_{prefix}_CO")),
                    "age_60_plus_count": number(row.get(f"AGRDE_60_ABOVE_{prefix}_CO")),
                    "loaded_at": loaded_at,
                }
            )
    return normalized


def validate_normalized(
    normalized: Mapping[str, Sequence[Mapping[str, Any]]], *, periods: Mapping[str, str]
) -> None:
    if not normalized["area_mapping"]:
        raise RuntimeError("GROUNDING_MAPPING_EMPTY")
    for table in ("seoul_cafe_store_fact", "seoul_cafe_sales_fact", "seoul_population_fact"):
        if not normalized[table]:
            raise RuntimeError(f"GROUNDING_TABLE_EMPTY:{table}")
    active_mapping = {
        str(row["admin_dong_code"])
        for row in normalized["area_mapping"]
        if row.get("deleted_date") is None
    }
    if not active_mapping:
        raise RuntimeError("GROUNDING_ACTIVE_MAPPING_EMPTY")
    for table, expected_period in (
        ("seoul_cafe_store_fact", periods["store"]),
        ("seoul_cafe_sales_fact", periods["sales"]),
    ):
        if any(str(row["period_code"]) != expected_period for row in normalized[table]):
            raise RuntimeError(f"GROUNDING_PERIOD_MISMATCH:{table}")
        if any(len(str(row["admin_dong_code"])) != 8 for row in normalized[table]):
            raise RuntimeError(f"GROUNDING_AREA_CODE_INVALID:{table}")


def latest_period(rows: Sequence[Mapping[str, Any]]) -> str:
    periods = {str(row.get("STDR_YYQU_CD", "")) for row in rows}
    valid = sorted(period for period in periods if len(period) == 5 and period.isdigit())
    if not valid:
        raise RuntimeError("SEOUL_PERIOD_MISSING")
    return valid[-1]


def latest_period_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    period = latest_period(rows)
    return [dict(row) for row in rows if str(row.get("STDR_YYQU_CD")) == period]


def number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def integer(value: Any) -> int | None:
    if value is None:
        return None
    return int(float(value))


def encode_ndjson(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json(row) + b"\n" for row in rows)


def decode_ndjson(payload: bytes) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], json.loads(line)) for line in payload.splitlines() if line]


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def upload_immutable(bucket: Any, object_name: str, payload: bytes) -> None:
    blob = bucket.blob(object_name)
    try:
        blob.upload_from_string(payload, if_generation_match=0)
    except Exception as error:
        if getattr(error, "code", None) != 412:
            raise
        existing = blob.download_as_bytes()
        if sha256(existing) != sha256(payload):
            raise RuntimeError(f"GROUNDING_IMMUTABLE_OBJECT_CONFLICT:{object_name}") from error


def main() -> None:
    GroundingIngestor(IngestionConfig.from_environment()).run()


if __name__ == "__main__":
    main()
