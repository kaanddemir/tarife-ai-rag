"""Tariffs the user points at by name ("Hoş Geldin 50 GB paketinin koşulları") or by
position in an earlier answer ("önceki ikinci tarifeyle karşılaştır").

Earlier answers are matched against catalog names only; an assistant turn is never
parsed for constraints. When a reference cannot be resolved unambiguously, the caller
gets a note asking the model to ask which tariff was meant -- it never guesses.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from app.models import ChatTurn
from app.rag.history import is_conversational


def _norm(text: str) -> str:
    text = text.replace("İ", "i").replace("I", "ı").lower()
    text = text.translate(str.maketrans("çğıöşü", "cgiosu"))
    return re.sub(r"[^a-z0-9+]+", " ", text).strip()


@lru_cache(maxsize=1)
def catalog_names() -> tuple[str, ...]:
    from app.ingest.load import build_catalog

    return tuple(sorted({r["name"] for r in build_catalog()[0]}, key=len, reverse=True))


def _distinctive(name: str) -> str:
    """The part of a name a person would type: drop trailing 'Paketi' / 'Tarifesi'."""
    return re.sub(r"\s+(paketi|tarifesi|tarife|kampanyasi)$", "", _norm(name))


def names_in(text: str) -> list[str]:
    """Catalog names mentioned in text, in order of first appearance (longest wins)."""
    low = f" {_norm(text)} "
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for name in catalog_names():
        key = _distinctive(name)
        if len(key.split()) < 2:
            continue
        for m in re.finditer(rf"(?<= ){re.escape(key)}(?= )", low):
            span = (m.start(), m.end())
            if not any(s < span[1] and span[0] < e for s, e in taken):
                taken.append(span)
                found.append((m.start(), name))
    return [n for _, n in sorted(found)]


_ORDINALS = {"ilk": 0, "birinci": 0, "ikinci": 1, "ucuncu": 2, "son": -1}
_REF_RE = re.compile(r"\b(ilk|birinci|ikinci|ucuncu|son)\w*\s+(?:tarife|paket|oneri|secenek)\w*|"
                     r"\b(onceki|bir onceki|ayni|o|bu)\s+(?:tarife|paket)\w*|\b(ikinci|ucuncu)(?:si|siyle|sini|ye|yle)\b")


def resolve(question: str, history: Optional[list[ChatTurn]]) -> tuple[list[str], Optional[str]]:
    """(names to pin, note). Names named in the question come first; an ordinal or
    'önceki tarife' reference is resolved against the last answer that named tariffs."""
    pinned = names_in(question)
    q = _norm(question)
    m = _REF_RE.search(q)
    if not m:
        return pinned, None
    previous: list[str] = []
    for turn in reversed(history or []):
        if turn.role == "assistant" and is_conversational(turn):
            previous = names_in(turn.content)
            if previous:
                break
    ordinal = m.group(1) or m.group(3)
    if ordinal in _ORDINALS:
        index = _ORDINALS[ordinal]
        if previous and (index == -1 or index < len(previous)):
            pinned.append(previous[index])
            return list(dict.fromkeys(pinned)), None
    elif len(previous) == 1:
        pinned.append(previous[0])
        return list(dict.fromkeys(pinned)), None
    return pinned, ("Kullanici onceki bir cevaptaki bir tarifeye atif yapti, ancak gecmisten hangisi oldugu kesin "
                    "belirlenemedi. Tahmin etme; hangi tarifeyi kastettigini adini yazarak belirtmesini kisaca iste.")
