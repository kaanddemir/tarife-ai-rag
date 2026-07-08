"""Hybrid retrieval: parse hard constraints from the question into SQL filters,
then rank the filtered set by pgvector cosine similarity to the query embedding.
Returns full typed Package rows so the API can render exact comparison tables
and ground citations on real values.
"""
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Package
from app.rag.embed import embed_query

# Operator aliases (Turkish, case/spacing tolerant) -> canonical key.
_OPERATOR_ALIASES = {
    "turkcell": "turkcell",
    "vodafone": "vodafone",
    "turk telekom": "turk_telekom",
    "türk telekom": "turk_telekom",
    "turktelekom": "turk_telekom",
    "tt": "turk_telekom",
}


def _detect_operator(q: str) -> Optional[str]:
    low = q.lower()
    for alias, canonical in _OPERATOR_ALIASES.items():
        if alias in low:
            return canonical
    return None


def _detect_max_price(q: str) -> Optional[float]:
    # "200 TL altinda", "under 200", "en fazla 150 tl", "150 liraya kadar"
    low = q.lower()
    if re.search(r"(alt[iı]|under|below|az|kadar|en fazla|maksimum|max)", low):
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:tl|₺|lira)?", low)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def _detect_gb(q: str) -> Optional[float]:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*gb", q.lower())
    return float(m.group(1).replace(",", ".")) if m else None


def retrieve(session: Session, question: str, k: Optional[int] = None) -> list[Package]:
    k = k or settings.top_k
    stmt = select(Package).where(Package.line_type == "prepaid")

    operator = _detect_operator(question)
    if operator:
        stmt = stmt.where(Package.operator == operator)

    max_price = _detect_max_price(question)
    if max_price is not None:
        stmt = stmt.where(Package.price_try <= max_price)

    # GB is treated as a soft target: keep packages within +/- 5 GB of the ask
    # so "compare 20 GB packages" returns the relevant band, not exact matches only.
    gb = _detect_gb(question)
    if gb is not None:
        stmt = stmt.where(Package.internet_gb >= gb - 5).where(Package.internet_gb <= gb + 5)

    # Semantic rank within the filtered set.
    query_vec = embed_query(question)
    stmt = stmt.order_by(Package.embedding.cosine_distance(query_vec)).limit(k)

    results = list(session.scalars(stmt).all())

    # Fallback: if filters were too strict and nothing matched, drop the price/GB
    # filters and rank purely semantically so the user still gets an answer.
    if not results:
        loose = (
            select(Package)
            .where(Package.line_type == "prepaid")
            .order_by(Package.embedding.cosine_distance(query_vec))
            .limit(k)
        )
        results = list(session.scalars(loose).all())

    return results
