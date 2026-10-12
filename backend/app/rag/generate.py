"""Generation: call Qwen via a hosted OpenAI-compatible API.

Grounded on the retrieved packages; returns the cited answer, the citations,
and the exact package rows used (for the comparison table).
"""
import logging
import re
from functools import lru_cache

import httpx
import openai
from openai import OpenAI

from app.config import settings
from app.models import ChatResponse, ChatTurn, Citation, Package, PackageOut
from app.rag.answer_check import guard, guard_clarification
from app.rag.prompt import build_clarification_messages, build_messages

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    # The SDK retries connection errors, 408/409/429 and 5xx with short exponential
    # backoff; other errors (401/403/400/404) fail at once. Bounded on both axes.
    return OpenAI(
        base_url=settings.provider_base_url,
        api_key=settings.provider_api_key,
        timeout=httpx.Timeout(settings.provider_timeout_seconds, connect=settings.provider_connect_timeout_seconds),
        max_retries=settings.provider_max_retries,
    )


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


class ProviderUnavailable(Exception):
    """The LLM provider call failed. Raised instead of returning an answer so the
    caller can tell a failure from a real reply (and not charge quota for it).
    `kind` is a short, non-sensitive label for logs and tests."""

    def __init__(self, kind: str = "error"):
        super().__init__(kind)
        self.kind = kind


def _classify(exc: Exception) -> str:
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIConnectionError):
        return "connection"
    if isinstance(exc, openai.RateLimitError):
        return "rate_limited"
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "auth"  # a configuration problem, not a transient one
    if isinstance(exc, openai.APIStatusError):
        return f"http_{exc.status_code}"
    return "unexpected"


def _complete(messages: list[dict], max_tokens: int) -> str:
    """Return the model's text; raise ProviderUnavailable if the call failed or
    returned nothing usable. Raw provider errors never reach the user."""
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
    except Exception as exc:  # noqa: BLE001 -- every failure maps to one public reply
        kind = _classify(exc)
        # Type and status only: provider messages can echo request details.
        log = logger.error if kind == "auth" else logger.warning
        log("LLM request failed: %s (%s)", kind, type(exc).__name__)
        raise ProviderUnavailable(kind) from exc
    try:
        content = resp.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        logger.warning("LLM returned a malformed completion")
        raise ProviderUnavailable("malformed") from None
    if not isinstance(content, str):
        raise ProviderUnavailable("malformed")
    answer = _strip_reasoning(content)
    if not answer:
        raise ProviderUnavailable("empty")
    return answer


def generate_clarification(
    question: str,
    history: list[ChatTurn] | None = None,
    missing: list[str] | None = None,
) -> ChatResponse:
    """Ask for the missing details instead of guessing. Raises ProviderUnavailable
    if the model call fails. No sources are sent to the
    model, so the reply can never carry a package -- citations/packages stay empty
    and the UI renders plain text with no comparison table."""
    messages = build_clarification_messages(question, history, missing)
    answer = guard_clarification(_complete(messages, max_tokens=300), missing or [])
    return ChatResponse(
        answer=answer,
        citations=[],
        packages=[],
    )


def generate_answer(
    question: str,
    packages: list[Package],
    history: list[ChatTurn] | None = None,
    notes: list[str] | None = None,
) -> ChatResponse:
    if not packages:
        return ChatResponse(
            answer="Bu soruya uygun bir paket bulunamadi.",
            citations=[],
            packages=[],
        )

    messages, citations = build_messages(question, packages, history, notes)
    packages = packages[:len(citations)]  # the sources actually shown to the model
    raw = _complete(messages, max_tokens=1100)
    # Commercial claims are checked against the same facts (rag.answer_check); the
    # user's own words are allowed numbers, assistant history is not.
    user_text = " ".join([question] + [t.content for t in (history or []) if t.role == "user"])
    checked = guard(raw, packages, user_text, notes)
    if checked.violations:
        logger.info("answer guard: %d issue(s), fallback=%s", len(checked.violations), checked.fallback)
    answer = checked.answer

    cited = _only_cited(citations, answer)
    return ChatResponse(
        answer=answer,
        citations=[Citation(**c) for c in cited],
        packages=[PackageOut.model_validate(p) for p in _only_cited_packages(packages, cited)],
    )
