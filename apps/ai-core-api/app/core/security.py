import uuid
from fastapi import Security, HTTPException, status, Header
from fastapi.security import APIKeyHeader
from app.core.config import get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def api_key_auth(
    api_key: str = Security(api_key_header),
    x_user_id: str = Header(None, alias="X-User-Id"),
):
    """Simple API key auth for development. Replace with Entra ID / JWT next."""
    settings = get_settings()
    fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    
    # Allow overriding user ID in debug/testing or if a valid API key is supplied
    target_user_id = fallback_user_id
    if x_user_id:
        try:
            target_user_id = uuid.UUID(x_user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-User-Id format. Must be a UUID."
            )

    if settings.debug and not api_key:
        # In debug mode, allow missing key but tag as anonymous
        return {"user_id": target_user_id, "email": "anonymous@local", "mode": "debug"}
    if api_key == settings.api_key:
        return {"user_id": target_user_id, "email": "api-key@local", "mode": "api-key"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key"
    )
