"""Sufficiency gate: prevents a recommendation before the user's need is clear.

This check runs BEFORE retrieval. When information is missing, `/chat` fetches no
packages and gives the model no sources; the model therefore cannot recommend a
package, because it has none. We cut the pipeline instead of trusting the prompt.

Slot detection reuses the existing detectors in `retrieve`; only the two signals
`retrieve` does not need (budget and usage intensity) are defined here.
"""
import re
from dataclasses import dataclass

from app.models import ChatTurn
from app.rag.history import contextual_question, is_conversational
from app.rag.retrieve import detect_gb, detect_line_type, detect_max_price, detect_operator

# "150 TL", "200 lira", "300₺" -- detect_max_price only fires when a bound word
# ("alti"/"kadar"/"en fazla") is present; this pattern treats any money mention
# as a budget signal ("butcem 200 TL civari").
_BUDGET_RE = re.compile(r"\d+\s*(?:tl|lira|₺)", re.I)

# Qualitative signals beyond a quantitative (GB) mention.
_USAGE_RE = re.compile(
    r"dakika|konu[sş]ma|sms|mesaj|s[ıi]n[ıi]rs[ıi]z|sosyal medya|"
    r"[cç]ok internet|az internet|yo[gğ]un kullan|az kullan|"
    r"video|dizi|film|youtube|tiktok|instagram|oyun",
    re.I,
)

# If the user leaves the decision to us, we don't force the gate.
_ESCAPE_RE = re.compile(
    r"fark ?etmez|farketmez|sen se[cç]|sen [oö]ner|hemen [oö]ner|"
    r"bilmiyorum|ne olursa|onemli degil|[oö]nemli de[gğ]il",
    re.I,
)

# A concrete question about the catalog (question particle, "?", or an info word).
_DIRECT_QUESTION_RE = re.compile(
    r"\?|\b(?:m[ıiuü]|m[ıiuü]d[ıiuü]r)\b|ne kadar|ka[cç] tl|nedir|hangi(?:si|leri)|ko[sş]ul|maliyet", re.I)

_CITATION_RE = re.compile(r"\[S\d+\]")

# Slot -> Turkish description handed to the model (so it asks for the missing slots).
SLOT_LABELS = {
    "line_type": "faturali mi faturasiz mi hat istedigi",
    "budget": "aylik butcesi",
    "usage": "kullanim onceligi (internet / dakika / SMS agirligi)",
}


def detect_budget_signal(q: str) -> bool:
    return bool(_BUDGET_RE.search(q)) or detect_max_price(q) is not None


def detect_usage_signal(q: str) -> bool:
    return detect_gb(q) is not None or bool(_USAGE_RE.search(q))


def detect_escape_hatch(q: str) -> bool:
    return bool(_ESCAPE_RE.search(q))


@dataclass(frozen=True)
class Slots:
    line_type: bool
    budget: bool
    usage: bool
    operator: bool
    escape_hatch: bool
    named: bool = False  # the user names a catalog tariff or points at an earlier one
    direct_question: bool = False

    @property
    def missing(self) -> list[str]:
        names = {"line_type": self.line_type, "budget": self.budget, "usage": self.usage}
        return [slot for slot, present in names.items() if not present]


def collect_slots(question: str, history: list[ChatTurn] | None = None) -> Slots:
    """Slots accumulate across turns: contextual_question merges recent user turns
    with the question, so the user can spread the info over several messages."""
    from app.rag.query import conversation_query
    from app.rag.references import resolve

    text = contextual_question(question, history)
    q = conversation_query(question, history)
    usage_slots = (q.min_gb, q.max_gb, q.unlimited_internet, q.unlimited_social, q.apps, q.subscriptions,
                   q.min_minutes, q.unlimited_minutes, q.min_sms, q.objective)
    return Slots(
        line_type=q.line_type is not None or detect_line_type(text) is not None,
        budget=q.max_price is not None or q.min_price is not None or detect_budget_signal(text),
        usage=detect_usage_signal(text) or any(v is not None for v in usage_slots),
        operator=detect_operator(text) is not None,
        escape_hatch=detect_escape_hatch(text),
        named=bool(resolve(question, history)[0]),
        direct_question=bool(_DIRECT_QUESTION_RE.search(question)),
    )


def already_clarified(history: list[ChatTurn] | None) -> bool:
    """The server is stateless; to allow at most one clarification turn we lean on an
    existing invariant: clarification replies never contain [S#] (that turn gets no
    sources). If the last assistant turn has no citation, we already asked once.

    Accepted limitation: a greeting reply also has no citation, so a vague message
    immediately after a greeting won't get a clarification question. Since the
    greeting reply already asks the same questions, no information is lost in practice.
    """
    if not history:
        return False
    for turn in reversed(history):
        # Error replies are not a clarification turn; skip them (is_conversational).
        if turn.role == "assistant" and is_conversational(turn):
            return not _CITATION_RE.search(turn.content)
    return False


def should_clarify(slots: Slots, history: list[ChatTurn] | None = None) -> bool:
    """Line type is required (eligibility filter); additionally at least one of the
    budget or usage signals is needed. Without both, a recommendation is just a guess."""
    if slots.escape_hatch or slots.named or already_clarified(history):
        return False
    if slots.line_type and slots.direct_question:
        return False  # a concrete question ("... yenileniyor mu?") is answered, not quizzed
    return not slots.line_type or not (slots.budget or slots.usage)
