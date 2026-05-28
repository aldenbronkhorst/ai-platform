import uuid
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.core.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def api_key_auth(api_key: str = Security(api_key_header)):
    """Simple API key auth for development. Replace with Entra ID / JWT next."""
    settings = get_settings()
    fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    if settings.debug and not api_key:
        # In debug mode, allow missing key but tag as anonymous
        return {"user_id": fallback_user_id, "email": "anonymous@local", "mode": "debug"}
    if api_key == settings.api_key:
        return {"user_id": fallback_user_id, "email": "api-key@local", "mode": "api-key"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key"
    )
