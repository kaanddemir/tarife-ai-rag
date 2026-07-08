"""Ingestion entrypoint: load the curated per-operator package data
(data/*.json), normalize, embed with BGE-M3, and upsert into Postgres/pgvector.

Run:  python -m app.ingest.load
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete

from app.db import SessionLocal, init_db
from app.ingest.normalize import normalize_record
from app.models import Package
from app.rag.embed import embed_texts

DATA_DIR = Path(__file__).parent / "data"
OPERATOR_FILES = ["turkcell.json", "vodafone.json", "turk_telekom.json"]


def load_records() -> list[dict]:
    """Read the per-operator curated data files and merge their package lists."""
    records: list[dict] = []
    for fname in OPERATOR_FILES:
        data = json.loads((DATA_DIR / fname).read_text(encoding="utf-8"))
        records.extend(data["packages"])
    return records


def run() -> int:
    init_db()
    records = [normalize_record(r) for r in load_records()]

    print(f"[load] embedding {len(records)} packages with BGE-M3...")
    vectors = embed_texts([r["doc_text"] for r in records])

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.execute(delete(Package))  # full refresh (small corpus)
        for rec, vec in zip(records, vectors):
            session.add(
                Package(
                    operator=rec["operator"],
                    name=rec["name"],
                    line_type=rec["line_type"],
                    package_group=rec.get("package_group"),
                    price_try=rec.get("price_try"),
                    price_period=rec.get("price_period"),
                    price_note=rec.get("price_note"),
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
                    faq=rec.get("faq"),
                    source_url=rec["source_url"],
                    listing_url=rec.get("listing_url"),
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
