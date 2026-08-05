"""Ingestion entrypoint: load the curated per-operator package data
(data/*.json), normalize, embed with BGE-M3, and upsert into Postgres/pgvector.

Each data file is one bucket -- operator x line_type x customer_type -- named
`{operator}_{line_type}_{customer_type}.json`. The file header is the single
source of truth for those three fields; per-package copies are overwritten.

Run:  python -m app.ingest.load
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from app.db import SessionLocal, reset_packages_table
from app.ingest.normalize import normalize_record
from app.models import Package
from app.rag.embed import embed_texts

DATA_DIR = Path(__file__).parent / "data"

# Key that identifies a package; the same package can appear in more than one
# customer-type list (see merge_customer_types).
_IDENTITY_FIELDS = ("operator", "name", "line_type")


def load_records() -> list[dict]:
    """Read every bucket file and stamp its header onto each package record."""
    records: list[dict] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        header = {k: data[k] for k in ("operator", "line_type", "customer_type")}
        for pkg in data["packages"]:
            records.append({**pkg, **header})
    return records


def merge_customer_types(records: list[dict]) -> list[dict]:
    """Collapse a package that appears in both the `new` and `existing` bucket
    into a single `any` row -- otherwise it is embedded twice and the chat cites
    the same package as two different sources."""
    merged: dict[tuple, dict] = {}
    for rec in records:
        key = tuple(rec[f] for f in _IDENTITY_FIELDS)
        seen = merged.get(key)
        if seen is None:
            merged[key] = rec
        elif seen["customer_type"] != rec["customer_type"]:
            seen["customer_type"] = "any"
    return list(merged.values())


def run() -> int:
    reset_packages_table()  # rebuild with the current schema (full refresh)
    records = [
        normalize_record(r, require_mobile_voice=True)
        for r in merge_customer_types(load_records())
    ]

    print(f"[load] embedding {len(records)} packages with BGE-M3...")
    vectors = embed_texts([r["doc_text"] for r in records])

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        # Table was just recreated by reset_packages_table(), so it starts empty.
        for rec, vec in zip(records, vectors):
            session.add(
                Package(
                    operator=rec["operator"],
                    name=rec["name"],
                    canonical_id=rec.get("canonical_id"),
                    line_type=rec["line_type"],
                    customer_type=rec["customer_type"],
                    package_group=rec.get("package_group"),
                    operator_family=rec.get("operator_family"),
                    audience_segment=rec.get("audience_segment"),
                    product_scope=rec.get("product_scope", "mobile_voice"),
                    age_min=rec.get("age_min"),
                    age_max=rec.get("age_max"),
                    eligibility_tags=rec.get("eligibility_tags"),
                    benefit_tags=rec.get("benefit_tags"),
                    price_try=rec.get("price_try"),
                    price_period=rec.get("price_period"),
                    commitment_months=rec.get("commitment_months"),
                    commitment_status=rec.get("commitment_status", "unknown"),
                    price_lock_months=rec.get("price_lock_months"),
                    no_commitment_price_try=rec.get("no_commitment_price_try"),
                    internet_gb=rec.get("internet_gb"),
                    social_media_gb=rec.get("social_media_gb"),
                    bonus_internet_gb=rec.get("bonus_internet_gb"),
                    total_internet_gb=rec.get("total_internet_gb"),
                    voice_minutes=rec.get("voice_minutes"),
                    sms_count=rec.get("sms_count"),
                    validity_value=rec.get("validity_value"),
                    validity_unit=rec.get("validity_unit"),
                    validity_days=rec.get("validity_days"),
                    conditions=rec.get("conditions"),
                    campaign=rec.get("campaign"),
                    source_url=rec["source_url"],
                    data_quality_status=rec.get("data_quality_status", "complete"),
                    missing_fields=rec.get("missing_fields"),
                    scraped_at=now,
                    doc_text=rec["doc_text"],
                    embedding=vec,
                )
            )
        session.commit()
    print(f"[load] upserted {len(records)} packages.")
    return len(records)


if __name__ == "__main__":
    run()
