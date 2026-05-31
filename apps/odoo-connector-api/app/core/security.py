import hmac
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from app.core.config import get_settings

api_key_header = APIKeyHeader(name="X-Internal-API-Key", auto_error=False)


async def internal_api_key_auth(api_key: str = Security(api_key_header)):
    settings = get_settings()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing internal API key"
        )
    if settings.internal_api_key and hmac.compare_digest(api_key, settings.internal_api_key):
        return {"mode": "internal", "service": "ai-core-api"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid internal API key"
    )
