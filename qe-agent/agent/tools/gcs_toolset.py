"""GCS toolset: inspect raw CSV landing zone (read-only).

Bucket layout (from synthetic-data-generator):
    gs://qe_hack_syndicate_raw/qe_hack_syndicate_raw/{customer,address,account}/*.csv
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from functools import lru_cache

from google.cloud import storage

from agent.config import SETTINGS
from agent.tools.credentials import get_credentials


@lru_cache(maxsize=1)
def _client() -> storage.Client:
    return storage.Client(project=SETTINGS.gcp_project, credentials=get_credentials())


def _prefixes(entity: str) -> list[str]:
    """Possible object prefixes for an entity (handles nested-bucket variant)."""
    return [f"{entity}/", f"{SETTINGS.gcs_bucket}/{entity}/"]


def list_csv_files(entity: str) -> dict:
    """List CSV objects for an entity (customer|address|account).

    Returns a dict with the object names, count, and newest object age.
    """
    bucket = _client().bucket(SETTINGS.gcs_bucket)
    blobs: list[storage.Blob] = []
    for prefix in _prefixes(entity):
        blobs.extend(b for b in bucket.list_blobs(prefix=prefix) if b.name.endswith(".csv"))
    blobs = list({b.name: b for b in blobs}.values())
    newest = max((b.updated for b in blobs), default=None)
    age_seconds = None
    if newest is not None:
        age_seconds = (datetime.now(timezone.utc) - newest).total_seconds()
    return {
        "entity": entity,
        "bucket": SETTINGS.gcs_bucket,
        "file_count": len(blobs),
        "files": sorted(b.name for b in blobs),
        "newest_updated": newest.isoformat() if newest else None,
        "newest_age_seconds": age_seconds,
    }


def read_csv_rows(object_name: str, limit: int = 50) -> dict:
    """Read up to `limit` rows from a CSV object as dicts."""
    blob = _client().bucket(SETTINGS.gcs_bucket).blob(object_name)
    text = blob.download_as_text()
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i >= limit:
            break
        rows.append(row)
    return {"object": object_name, "header": reader.fieldnames, "rows": rows}


def count_csv_rows(entity: str) -> dict:
    """Total data-row count across all CSV files for an entity."""
    bucket = _client().bucket(SETTINGS.gcs_bucket)
    seen: dict[str, storage.Blob] = {}
    for prefix in _prefixes(entity):
        for b in bucket.list_blobs(prefix=prefix):
            if b.name.endswith(".csv"):
                seen[b.name] = b
    total = 0
    for blob in seen.values():
        text = blob.download_as_text()
        total += max(sum(1 for _ in io.StringIO(text)) - 1, 0)  # minus header
    return {"entity": entity, "files": len(seen), "row_count": total}
