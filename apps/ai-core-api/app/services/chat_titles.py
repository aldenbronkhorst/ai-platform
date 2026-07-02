import re
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

CHAT_TITLE_MAX_CHARS = 70

TITLE_GENERATION_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with the "
    "following exchange. The title should capture the main topic or intent. "
    "Correct obvious spelling mistakes in the title. "
    "Write the title in the same language the user is writing in. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


def _sanitize_chat_title(title: Any) -> str | None:
    text = str(title or "").strip()
    text = re.sub(r"[\r\n]+.*$", "", text).strip()
    text = re.sub(r"^(title|chat title)\s*[:\-]\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^\d+[\).\s-]+", "", text).strip()
    text = text.strip("\"'`*_ ")
    text = text.rstrip(".:;,- ")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if "<|" in text or "|>" in text:
        return None
    if text.lower() in {"new chat", "untitled", "chat", "conversation"}:
        return None
    if len(text) > CHAT_TITLE_MAX_CHARS:
        text = text[:CHAT_TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip() or text[:CHAT_TITLE_MAX_CHARS].strip()
    return text[:1].upper() + text[1:]


def _message_text(message: dict[str, Any]) -> str:
    text = str(message.get("content") or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _first_message_text(messages: list[dict[str, Any]], role: str) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == role:
            text = _message_text(message)
            if text:
                return text
    return ""


def _title_exchange(messages: list[dict[str, Any]]) -> str | None:
    user_text = _first_message_text(messages, "user")
    if not user_text:
        return None
    assistant_text = _first_message_text(messages, "assistant")
    exchange = f"User: {user_text[:500]}"
    if assistant_text:
        exchange += f"\n\nAssistant: {assistant_text[:500]}"
    return exchange


async def generate_chat_title(db: AsyncSession, messages: list[dict[str, Any]]) -> str | None:
    exchange = _title_exchange(messages)
    if not exchange:
        return None

    from app.services.model_router import build_model_client, get_enabled_route

    route, model, _provider = await get_enabled_route(db, "general_chat")
    client = await build_model_client(_provider, model)
    result = await client.chat_completion(
        [
            {"role": "system", "content": TITLE_GENERATION_PROMPT},
            {"role": "user", "content": exchange},
        ],
        temperature=float(route.temperature) if route.temperature is not None else 0.3,
        max_tokens=200,
        tools=None,
    )
    if result.get("error"):
        return None
    return _sanitize_chat_title(result.get("content"))
