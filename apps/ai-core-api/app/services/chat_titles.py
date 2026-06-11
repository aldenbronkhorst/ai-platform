import re
from typing import Any

CHAT_TITLE_MAX_CHARS = 70
CHAT_TITLE_CANONICAL_WORDS = {
    "ai": "AI",
    "api": "API",
    "azure": "Azure",
    "ci": "CI",
    "entra": "Entra",
    "exchange": "Exchange",
    "github": "GitHub",
    "intune": "Intune",
    "m365": "M365",
    "odoo": "Odoo",
    "mcp": "MCP",
    "ms": "MS",
    "ocr": "OCR",
    "pdf": "PDF",
    "po": "PO",
    "pr": "PR",
    "sharepoint": "SharePoint",
    "teams": "Teams",
    "ui": "UI",
}
CHAT_TITLE_STOPWORDS = {
    "a", "all", "an", "and", "are", "as", "at", "be", "can", "check", "could",
    "did", "do", "does", "for", "from", "get", "give", "how", "i", "if", "in",
    "is", "it", "list", "me", "my", "now", "of", "on", "or", "our", "please",
    "show", "tell", "that", "the", "there", "this", "to", "today", "us", "was",
    "we", "were", "what", "when", "where", "why", "with", "would", "you", "your",
}
CHAT_TITLE_WORD_REPLACEMENTS = {
    "acess": "access",
    "employe": "employee",
    "faliours": "failures",
    "halllucinations": "hallucinations",
    "connecotrs": "connectors",
    "uerer": "user",
    "whats": "what",
}


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


def _title_word(token: str) -> str:
    normalized = CHAT_TITLE_WORD_REPLACEMENTS.get(token.lower(), token)
    canonical = CHAT_TITLE_CANONICAL_WORDS.get(normalized.lower())
    if canonical:
        return canonical
    if normalized.isupper() and len(normalized) <= 6:
        return normalized
    return normalized[:1].upper() + normalized[1:].lower()


def _normalize_title_text(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[_*`~#>\[\]{}()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_tokens(text: str) -> list[str]:
    normalized = _normalize_title_text(text)
    return re.findall(r"[A-Za-z][A-Za-z0-9'-]*|\d+", normalized)


def _first_user_title_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            text = _normalize_title_text(str(message.get("content") or ""))
            if text:
                return text
    return ""


def _fallback_chat_title(messages: list[dict[str, Any]]) -> str | None:
    """Create a concise local title from the first user message."""
    first_user_text = _first_user_title_text(messages)
    if not first_user_text:
        return None

    tokens = _title_tokens(first_user_text)
    useful: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lower = CHAT_TITLE_WORD_REPLACEMENTS.get(token.lower(), token.lower())
        if lower in CHAT_TITLE_STOPWORDS or token.isdigit() or lower in seen:
            continue
        seen.add(lower)
        useful.append(token)
        if len(useful) >= 6:
            break

    selected = useful[:6] or tokens[:6]
    title = _sanitize_chat_title(" ".join(_title_word(token) for token in selected))
    if title:
        return title
    return "New Chat"


async def generate_chat_title(messages: list[dict[str, Any]]) -> str | None:
    return _fallback_chat_title(messages)
