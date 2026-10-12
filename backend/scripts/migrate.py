"""Install the current TarifeAI schema into a fresh PostgreSQL database.

    python scripts/migrate.py --database-url-env DB_URL install

This is a clean-install runner, not a historical migration replay tool. The baseline
refuses to run if any TarifeAI application table already exists and creates no migration
ledger. Catalog ingestion remains a separate explicit step.
"""
import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine

BASELINE = Path(__file__).resolve().parents[1] / "schema" / "current_baseline.sql"


def install(database_url: str, baseline: Path = BASELINE) -> None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(baseline.read_text(encoding="utf-8"))
    finally:
        engine.dispose()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument("--database-url")
    database.add_argument("--database-url-env", metavar="NAME")
    parser.add_argument("command", choices=["install"])
    args = parser.parse_args(argv)
    database_url = args.database_url
    if args.database_url_env:
        database_url = os.environ.get(args.database_url_env)
        if not database_url:
            parser.error(f"environment variable {args.database_url_env!r} is not set")
    install(database_url)
    print("installed current TarifeAI baseline (catalog not ingested)")


if __name__ == "__main__":
    sys.exit(main())
