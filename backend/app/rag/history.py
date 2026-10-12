"""Small helpers for bounded chat history context."""
import re

from app.models import ChatTurn

HISTORY_LIMIT = 5

# Older clients returned this (HTTP 200) when the LLM provider failed; /chat now answers
# 503 instead. Conversations saved in browsers may still contain it, so history keeps
# recognising it.
PROVIDER_ERROR_MESSAGE = "Su anda yanit uretilemiyor, lutfen biraz sonra tekrar deneyin."

# Assistant turns that carry no conversation content: the provider error above and
# the frontend's own local error bubbles (frontend/app/page.tsx). They are dropped
# from history so they neither reach the prompt nor count as a clarification turn.
_NON_CONVERSATIONAL_REPLIES = {
    PROVIDER_ERROR_MESSAGE,
    "Şu an yanıt veremedim. Lütfen birazdan tekrar dene.",
    "Sunucuya ulaşılamadı. İnternet bağlantını kontrol edip tekrar dener misin?",
    "Günlük mesaj limitine ulaştın. Yarın tekrar deneyebilirsin.",
    "Oturumun doğrulanamadı. Sayfayı yenileyip tekrar dener misin?",
}


def is_conversational(turn: ChatTurn) -> bool:
    content = turn.content.strip()
    if not content:
        return False
    return not (turn.role == "assistant" and content in _NON_CONVERSATIONAL_REPLIES)


def recent_history(history: list[ChatTurn] | None) -> list[ChatTurn]:
    if not history:
        return []
    return [turn for turn in history if is_conversational(turn)][-HISTORY_LIMIT:]


def contextual_question(question: str, history: list[ChatTurn] | None) -> str:
    """Add recent user turns to retrieval without treating assistant text as facts."""
    user_context = [
        turn.content.strip()
        for turn in recent_history(history)
        if turn.role == "user" and turn.content.strip()
    ]
    if not user_context:
        return question
    return "\n".join([*user_context, question])


_SOURCE_MARKER_RE = re.compile(r"\s*\[S\d+(?:\s*[–-]\s*S\d+)?\]")


def format_history(history: list[ChatTurn] | None) -> str:
    """Earlier turns for the prompt. [S#] markers are stripped from earlier answers:
    they pointed at THAT turn's sources, and would collide with this turn's ids."""
    labels = {"user": "Kullanıcı", "assistant": "Asistan"}
    lines = [
        f"- {labels[turn.role]}: {_SOURCE_MARKER_RE.sub('', turn.content.strip())}"
        for turn in recent_history(history)
    ]
    return "\n".join(lines)
