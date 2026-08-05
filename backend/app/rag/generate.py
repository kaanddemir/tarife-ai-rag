"""Generation: call Qwen via a hosted OpenAI-compatible API.

Grounded on the retrieved packages; returns the cited answer, the citations,
and the exact package rows used (for the comparison table).
"""
import logging
import re
from functools import lru_cache

from openai import OpenAI

from app.config import settings
from app.models import ChatResponse, ChatTurn, Citation, Package, PackageOut
from app.rag.prompt import build_clarification_messages, build_messages

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(base_url=settings.provider_base_url, api_key=settings.provider_api_key)


# Hard cap on how many packages a single answer can recommend. The prompt already
# asks for at most one main pick plus two alternatives, but that is advisory; this
# enforces it deterministically so a non-compliant model can't flood the table.
# Citations keep the answer's order (main recommendation first), so the first N are
# the intended picks.
MAX_RECOMMENDATIONS = 3


def _only_cited(citations: list[dict], answer: str) -> list[dict]:
    """Keep citations actually referenced in the answer, capped at MAX_RECOMMENDATIONS.
    If the answer cites nothing at all (greeting / off-topic / clarifying question),
    return none so the UI shows no comparison. Only fall back to all when the model
    emitted markers we couldn't map."""
    cited_by_id = {c["id"]: c for c in citations}
    used_ids = []
    for match in re.finditer(r"\[(S\d+)\]", answer):
        sid = match.group(1)
        if sid in cited_by_id and sid not in used_ids:
            used_ids.append(sid)
    if used_ids:
        return [cited_by_id[sid] for sid in used_ids[:MAX_RECOMMENDATIONS]]
    return citations[:MAX_RECOMMENDATIONS] if re.search(r"\[S\d+\]", answer) else []


def _only_cited_packages(packages: list[Package], cited: list[dict]) -> list[Package]:
    if not cited:
        return []
    package_by_sid = {
        f"S{index}": package
        for index, package in enumerate(packages, start=1)
    }
    used = [package_by_sid[c["id"]] for c in cited if c["id"] in package_by_sid]
    return used or packages


def _strip_reasoning(answer: str) -> str:
    """Hide reasoning blocks emitted by reasoning-capable open models."""
    return re.sub(r"(?is)<think>.*?</think>", "", answer).strip()


_PROVIDER_ERROR = "Su anda yanit uretilemiyor, lutfen biraz sonra tekrar deneyin."


def _complete(messages: list[dict], max_tokens: int) -> str | None:
    """Return the model's text, or None if the provider call failed."""
    try:
        resp = _client().chat.completions.create(
            model=settings.model,
            messages=messages,
            temperature=0.2,
            max_tokens=max_tokens,
            extra_body={
                "reasoning_format": "hidden",
                "reasoning_effort": "none",
            },
        )
    except Exception as exc:  # provider timeout / 5xx / auth / rate limit
        logger.warning("LLM request failed: %s: %s", type(exc).__name__, exc)
        return None
    return _strip_reasoning(resp.choices[0].message.content or "")


def generate_clarification(
    question: str,
    history: list[ChatTurn] | None = None,
    missing: list[str] | None = None,
) -> ChatResponse:
    """Ask for the missing details instead of guessing. No sources are sent to the
    model, so the reply can never carry a package -- citations/packages stay empty
    and the UI renders plain text with no comparison table."""
    messages = build_clarification_messages(question, history, missing)
    answer = _complete(messages, max_tokens=300)
    return ChatResponse(
        answer=answer or _PROVIDER_ERROR,
        citations=[],
        packages=[],
    )


def generate_answer(
    question: str,
    packages: list[Package],
    history: list[ChatTurn] | None = None,
) -> ChatResponse:
    if not packages:
        return ChatResponse(
            answer="Bu soruya uygun bir paket bulunamadi.",
            citations=[],
            packages=[],
        )

    messages, citations = build_messages(question, packages, history)
    answer = _complete(messages, max_tokens=1100)
    if answer is None:
        return ChatResponse(answer=_PROVIDER_ERROR, citations=[], packages=[])

    cited = _only_cited(citations, answer)
    return ChatResponse(
        answer=answer,
        citations=[Citation(**c) for c in cited],
        packages=[PackageOut.model_validate(p) for p in _only_cited_packages(packages, cited)],
    )
