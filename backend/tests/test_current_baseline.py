"""Offline checks that keep the fresh-install baseline aligned with the ORM."""
from pathlib import Path
import re

from app.models import Base


BASELINE = Path(__file__).resolve().parents[1] / "schema" / "current_baseline.sql"


def _sql() -> str:
    return BASELINE.read_text(encoding="utf-8")


def test_baseline_contains_every_model_table_column_constraint_and_index():
    sql = _sql()
    for table in Base.metadata.sorted_tables:
        assert f"CREATE TABLE public.{table.name}" in sql
        for column in table.columns:
            assert re.search(rf"\b{re.escape(column.name)}\s", sql)
        for constraint in table.constraints:
            if constraint.name:
                assert constraint.name in sql
        for index in table.indexes:
            assert index.name in sql


def test_baseline_has_current_rls_boundary_and_no_legacy_ledger():
    sql = _sql()
    for table in ("packages", "message_usage", "quota_reservation"):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in sql
    assert len(re.findall(r"^CREATE POLICY ", sql, re.MULTILINE)) == 7
    assert "REVOKE ALL PRIVILEGES ON public.packages" in sql
