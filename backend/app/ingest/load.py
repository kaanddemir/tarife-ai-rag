"""Ingestion entrypoint: load the curated per-operator package data
(data/*.json), normalize, validate, embed with BGE-M3, and atomically replace the
catalog in Postgres/pgvector.

Each data file is one bucket -- operator x line_type x customer_type -- named
`{operator}_{line_type}_{customer_type}.json`. The file header is the single
source of truth for those three fields; per-package copies are overwritten.

Safety order (nothing touches the database until step 4):
  1. load + per-bucket duplicate check     3. embed + vector validation
  2. normalize + validate every record      4. one transaction: check schema,
                                               DELETE old rows, INSERT new rows,
                                               verify, COMMIT (any error -> ROLLBACK)
Readers keep seeing the previous catalog until the commit. The table is never
dropped; a schema mismatch fails loudly instead. `message_usage` is never touched.

Run:  python -m app.ingest.load                   (authorized environment only)
      python -m app.ingest.load --dry-run         (validate + report, no embedding/DB)
"""
import argparse
import hashlib
import json
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest.normalize import normalize_record
from app.ingest.validate import (
    CatalogValidationError,
    ValidationReport,
    find_duplicate_identities,
    validate_catalog,
    validate_embeddings,
    validate_provenance,
)
from app.models import Package

DATA_DIR = Path(__file__).parent / "data"

# Key that identifies a package; the same package can appear in more than one
# customer-type list (see merge_customer_types).
_IDENTITY_FIELDS = ("operator", "name", "line_type")

# Fixed namespace so a package keeps the same id across ingests (frontend-held ids
# and /compare references survive a catalog refresh).
_PACKAGE_ID_NAMESPACE = uuid.UUID("6f1c2a52-6a8e-4c43-9a43-2f0b8f0e7d10")


class SchemaMismatchError(RuntimeError):
    """The live `packages` table does not match the model; refuse to touch it."""


def package_id(operator: str, line_type: str, name: str) -> uuid.UUID:
    return uuid.uuid5(_PACKAGE_ID_NAMESPACE, f"{operator}|{line_type}|{name}")


def load_records() -> list[dict]:
    """Read every bucket file and stamp its header onto each package record."""
    records: list[dict] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        header = {k: data[k] for k in ("operator", "line_type", "customer_type")}
        captured = data.get("scraped_at")
        for pkg in data["packages"]:
            records.append({**pkg, **header, "_source_captured_at": captured})
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


def build_catalog(raw: Optional[list[dict]] = None) -> tuple[list[dict], ValidationReport]:
    """Steps 1-2: load, reject duplicates inside a bucket (merge_customer_types
    would otherwise silently keep the first), check every record's provenance
    (verified_at + evidence), then normalize and validate. Every record in the data files is a
    verified current offer -- there is no inactive record to filter out."""
    raw = load_records() if raw is None else raw
    duplicates = find_duplicate_identities(raw)
    if duplicates:
        raise CatalogValidationError(duplicates)
    provenance = ValidationReport()
    for rec in raw:
        validate_provenance(rec, provenance)
    if provenance.errors:
        raise CatalogValidationError(provenance.errors)
    try:
        records = [normalize_record(r, require_mobile_voice=True) for r in merge_customer_types(raw)]
    except ValueError as exc:  # enum / scope errors raised by normalize_record
        raise CatalogValidationError([str(exc)]) from exc
    report = validate_catalog(records)
    return records, report


def _captured_at(rec: dict) -> datetime:
    """When the record's facts were last checked on the source (UTC midnight): its
    verified_at (required by validation). Never the ingest time, so freshness cannot
    be faked by re-running the loader."""
    raw = rec.get("verified_at") or rec.get("_source_captured_at")
    if raw:
        return datetime.combine(datetime.fromisoformat(raw).date(), time.min, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def doc_hash(doc_text: str) -> str:
    return hashlib.sha256(doc_text.encode("utf-8")).hexdigest()


def _row(rec: dict, vec: list[float]) -> Package:
    return Package(
        id=package_id(rec["operator"], rec["line_type"], rec["name"]),
        operator=rec["operator"],
        name=rec["name"],
        line_type=rec["line_type"],
        customer_type=rec["customer_type"],
        operator_family=rec.get("operator_family"),
        audience_segment=rec.get("audience_segment"),
        product_scope=rec.get("product_scope", "mobile_voice"),
        age_min=rec.get("age_min"),
        age_max=rec.get("age_max"),
        eligibility_tags=rec.get("eligibility_tags"),
        activation_channels=rec.get("activation_channels"),
        benefits=rec.get("benefits"),
        benefits_status=rec.get("benefits_status") or "unverified",
        price_try=rec.get("price_try"),
        price_period=rec.get("price_period"),
        renewal_type=rec.get("renewal_type") or "unknown",
        commitment_months=rec.get("commitment_months"),
        commitment_status=rec.get("commitment_status", "unknown"),
        price_lock_months=rec.get("price_lock_months"),
        no_commitment_price_try=rec.get("no_commitment_price_try"),
        promo_months=rec.get("promo_months"),
        price_after_promo_try=rec.get("price_after_promo_try"),
        internet_gb=rec.get("internet_gb"),
        social_media_gb=rec.get("social_media_gb"),
        social_media_apps=rec.get("social_media_apps"),
        bonus_internet_gb=rec.get("bonus_internet_gb"),
        bonus_internet_scope=rec.get("bonus_internet_scope"),
        voice_minutes=rec.get("voice_minutes"),
        sms_count=rec.get("sms_count"),
        validity_days=rec.get("validity_days"),
        conditions=rec.get("conditions"),
        campaign=rec.get("campaign"),
        source_url=rec["source_url"],
        # The data snapshot's date, not the ingest time (freshness must not be faked).
        verified_at=_captured_at(rec),
        doc_text=rec["doc_text"],
        embedding=vec,
        embedding_model=settings.embed_model,
        doc_hash=doc_hash(rec["doc_text"]),
    )


def check_schema(session: Session) -> None:
    """Fail clearly if `packages` is absent or differs from the application model."""
    bind = session.connection()
    insp = inspect(bind)
    if not insp.has_table(Package.__tablename__):
        raise SchemaMismatchError(
            "packages table is missing. Install backend/schema/current_baseline.sql first."
        )
    live = {c["name"] for c in insp.get_columns(Package.__tablename__)}
    expected = {c.name for c in Package.__table__.columns}
    if live != expected:
        raise SchemaMismatchError(
            "packages schema differs from app.models.Package "
            f"(missing: {sorted(expected - live)}, unexpected: {sorted(live - expected)}). "
            "Apply a reviewed forward schema change before ingesting."
        )
    dims = bind.execute(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'packages'::regclass AND attname = 'embedding'"
        )
    ).scalar()
    if dims != settings.embed_dim:
        raise SchemaMismatchError(f"packages.embedding is vector({dims}), expected vector({settings.embed_dim})")


def replace_catalog(
    engine: Engine,
    records: list[dict],
    vectors: list[list[float]],
    *,
    before_commit: Optional[Callable[[Session], None]] = None,
) -> int:
    """Step 4: atomically swap the catalog. DELETE (not TRUNCATE) so concurrent
    readers keep the old rows until commit instead of blocking on a lock."""
    with Session(engine) as session, session.begin():
        check_schema(session)
        session.execute(delete(Package))
        session.add_all(_row(rec, vec) for rec, vec in zip(records, vectors))
        session.flush()
        stored = session.scalar(select(func.count()).select_from(Package))
        missing = session.scalar(select(func.count()).select_from(Package).where(Package.embedding.is_(None)))
        if stored != len(records) or missing:
            raise RuntimeError(f"post-insert check failed: {stored} rows, {missing} without embedding")
        if before_commit:
            before_commit(session)
    return len(records)


def run(
    engine: Optional[Engine] = None,
    embed: Optional[Callable[[list[str]], list]] = None,
    *,
    before_commit: Optional[Callable[[Session], None]] = None,
) -> int:
    records, report = build_catalog()
    print(f"[load] {len(records)} packages valid ({len(report.warnings)} warnings).")

    if embed is None:
        from app.rag.embed import embed_texts as embed
    print(f"[load] embedding {len(records)} packages...")
    vectors = validate_embeddings(embed([r["doc_text"] for r in records]), len(records), settings.embed_dim)

    if engine is None:
        from app.db import engine
    count = replace_catalog(engine, records, vectors, before_commit=before_commit)
    print(f"[load] replaced catalog atomically: {count} packages.")
    return count


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="validate and report only; no embedding, no DB")
    args = parser.parse_args()
    if args.dry_run:
        records, report = build_catalog()
        for warning in report.warnings:
            print(f"[warn] {warning}")
        print(f"[dry-run] {len(records)} packages valid, {len(report.warnings)} warnings. No changes made.")
        return
    run()


if __name__ == "__main__":
    _main()
