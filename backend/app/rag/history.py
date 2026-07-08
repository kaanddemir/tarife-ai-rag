"""Small helpers for bounded chat history context."""

from app.models import ChatTurn

HISTORY_LIMIT = 5


def recent_history(history: list[ChatTurn] | None) -> list[ChatTurn]:
    if not history:
        return []
    return [turn for turn in history if turn.content.strip()][-HISTORY_LIMIT:]


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


def format_history(history: list[ChatTurn] | None) -> str:
    labels = {"user": "Kullanıcı", "assistant": "Asistan"}
    lines = [
        f"- {labels[turn.role]}: {turn.content.strip()}"
        for turn in recent_history(history)
    ]
    return "\n".join(lines)
