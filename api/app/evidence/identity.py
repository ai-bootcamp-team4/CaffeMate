from typing import Any

import rfc8785


def immutable_evidence_record_bytes(record: dict[str, Any]) -> bytes:
    """Canonical Evidence bytes excluding per-observation timestamps.

    ``retrieved_at`` and ``source.source_observed_at`` describe when a source was
    observed by a particular retrieval call. They do not change the immutable
    fact identity of an EvidenceRecord. All other fields remain part of the
    collision boundary so a reused evidence_id cannot hide changed content.
    """
    stable = dict(record)
    stable.pop("retrieved_at", None)
    source = stable.get("source")
    if isinstance(source, dict):
        stable["source"] = {
            key: value for key, value in source.items() if key != "source_observed_at"
        }
    return rfc8785.dumps(stable)
