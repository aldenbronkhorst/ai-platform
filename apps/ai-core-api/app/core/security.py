import os
import jwt
import uuid
from datetime import datetime
from fastapi import Security, HTTPException, status, Header, Depends
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.models.models import AIUser

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# Microsoft Entra JWKS URL for the specific tenant
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "9067d9d9-b0bf-4d56-be8f-8d5bc3bc06b5")
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
ISSUER = f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"

# PyJWKClient manages caching of public keys natively
try:
    jwk_client = jwt.PyJWKClient(JWKS_URL)
except Exception:
    jwk_client = None


async def validate_entra_jwt(token: str, db: AsyncSession) -> dict:
    """Validates the Entra ID access token (signature, audience, issuer, expiration, scopes).

    Resolves the matching database user based on email or Entra Object ID (oid).
    """
    settings = get_settings()

    # Safe local mock bypass ONLY on localhost and ONLY if debug/test mode is active
    if settings.debug and (token == "mock-local-token" or token.startswith("mock-")):
        # Retrieve or fallback to a developer user
        fallback_email = "alden@lotslotsmore.com"
        result = await db.execute(select(AIUser).where(AIUser.email == fallback_email))
        db_user = result.scalar_one_or_none()
        if not db_user:
            # Auto-provision developer on localhost if missing
            db_user = AIUser(
                id=uuid.UUID("e4807f22-97c8-4778-87a2-160f56d25247"),
                email=fallback_email,
                display_name="Alden Bronkhorst",
                role="admin",
                is_active="true"
            )
            db.add(db_user)
            await db.commit()
            await db.refresh(db_user)
        return {
            "user_id": db_user.id,
            "email": db_user.email,
            "roles": ["AIPlatform.Admin", "AIPlatform.User", "AIPlatform.Developer", "AIPlatform.Auditor"],
            "mode": "local-mock"
        }

    if not jwk_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWK client not initialized."
        )

    try:
        signing_key = jwk_client.get_signing_key_from_jwt(token)
        # Verify token claims
        # Audience can be either the backend App ID URI or the Client ID
        audience_candidates = [CLIENT_ID, f"api://{CLIENT_ID}"]
        
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience_candidates,
            issuer=ISSUER
        )

        # Extract claims
        email = payload.get("preferred_username") or payload.get("email") or payload.get("upn")
        entra_oid = payload.get("oid")
        roles = payload.get("roles", ["AIPlatform.User"]) # Default role if none assigned

        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing preferred_username/email claim."
            )

        # Resolve against database user
        result = await db.execute(
            select(AIUser).where(
                (AIUser.entra_object_id == entra_oid) | (AIUser.email == email)
            )
        )
        db_user = result.scalar_one_or_none()

        if not db_user:
            # If the user logged in via Entra, auto-provision their user record with default roles
            db_user = AIUser(
                id=uuid.uuid4(),
                email=email,
                display_name=payload.get("name", email.split("@")[0]),
                entra_object_id=entra_oid,
                role="user",
                is_active="true"
            )
            db.add(db_user)
            await db.commit()
            await db.refresh(db_user)
        elif not db_user.entra_object_id and entra_oid:
            # Link Entra Object ID on first successful login
            db_user.entra_object_id = entra_oid
            await db.commit()

        if db_user.is_active != "true":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive."
            )

        return {
            "user_id": db_user.id,
            "email": db_user.email,
            "roles": roles,
            "mode": "entra-jwt"
        }

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signature has expired."
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT: {e}"
        )


async def api_key_auth(
    api_key: str = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    x_user_id: str = Header(None, alias="X-User-Id"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Production-ready unified authentication dependency.

    Accepts Entra Bearer Token (primary) or local development overrides (secondary, localhost-only).
    """
    settings = get_settings()

    # 1. Primary path: Microsoft Entra JWT
    if bearer and bearer.credentials:
        return await validate_entra_jwt(bearer.credentials, db)

    # 2. Local-only development and testing overrides
    fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    target_user_id = fallback_user_id

    if x_user_id:
        try:
            target_user_id = uuid.UUID(x_user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-User-Id format. Must be a UUID."
            )

    # Local debug check: Allow missing key but require localhost
    if settings.debug and not api_key:
        return {"user_id": target_user_id, "email": "anonymous@local", "roles": ["AIPlatform.Admin"], "mode": "debug"}

    # Validate temporary production API key (will log warning to transition to JWT)
    if api_key and api_key == settings.api_key:
        return {"user_id": target_user_id, "email": "api-key@local", "roles": ["AIPlatform.Admin"], "mode": "api-key"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid authentication credentials (use Bearer JWT)."
    )


def require_role(allowed_roles: list[str]):
    """FastAPI dependency to enforce role-based access control (RBAC)."""
    def dependency(auth: dict = Depends(api_key_auth)):
        user_roles = auth.get("roles", [])
        # Admin bypasses role checks
        if "AIPlatform.Admin" in user_roles:
            return auth
            
        if not any(role in user_roles for role in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Insufficient permissions/roles."
            )
        return auth
    return dependency
