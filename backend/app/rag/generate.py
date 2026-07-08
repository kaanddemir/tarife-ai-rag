"""Generation: call Qwen via a hosted OpenAI-compatible API.

Grounded on the retrieved packages; returns the cited answer, the citations,
and the exact package rows used (for the comparison table).
"""
import re
from functools import lru_cache

from openai import OpenAI

from app.config import settings
from app.models import ChatResponse, ChatTurn, Citation, Package, PackageOut
from app.rag.prompt import build_messages


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(base_url=settings.provider_base_url, api_key=settings.provider_api_key)


def _only_cited(citations: list[dict], answer: str) -> list[dict]:
    """Keep citations actually referenced in the answer; if none matched
    (model omitted markers), keep all so the UI still links sources."""
    cited_by_id = {c["id"]: c for c in citations}
    used_ids = []
    for match in re.finditer(r"\[(S\d+)\]", answer):
        sid = match.group(1)
        if sid in cited_by_id and sid not in used_ids:
            used_ids.append(sid)
    return [cited_by_id[sid] for sid in used_ids] or citations


def _only_cited_packages(packages: list[Package], cited: list[dict]) -> list[Package]:
    package_by_sid = {
        f"S{index}": package
        for index, package in enumerate(packages, start=1)
    }
    used = [package_by_sid[c["id"]] for c in cited if c["id"] in package_by_sid]
    return used or packages


def _strip_reasoning(answer: str) -> str:
    """Hide reasoning blocks emitted by reasoning-capable open models."""
    return re.sub(r"(?is)<think>.*?</think>", "", answer).strip()


def generate_answer(
    question: str,
    packages: list[Package],
    history: list[ChatTurn] | None = None,
) -> ChatResponse:
    messages, citations = build_messages(question, packages, history)

    if not packages:
        return ChatResponse(
            answer="Bu soruya uygun bir faturasiz paket bulunamadi.",
            citations=[],
            packages=[],
        )

    try:
        resp = _client().chat.completions.create(
            model=settings.model,
            messages=messages,
            temperature=0.2,
            max_tokens=1100,
            extra_body={
                "reasoning_format": "hidden",
                "reasoning_effort": "none",
            },
        )
    except Exception as exc:  # provider timeout / 5xx / auth / rate limit
        print(f"[generate] LLM request failed: {type(exc).__name__}: {exc}")
        return ChatResponse(
            answer="Su anda yanit uretilemiyor, lutfen biraz sonra tekrar deneyin.",
            citations=[],
            packages=[],
        )
    answer = _strip_reasoning(resp.choices[0].message.content or "")
    cited = _only_cited(citations, answer)

    return ChatResponse(
        answer=answer,
        citations=[Citation(**c) for c in cited],
        packages=[PackageOut.model_validate(p) for p in _only_cited_packages(packages, cited)],
    )
