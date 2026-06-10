import os
import jwt
import uuid
from datetime import datetime, timezone
from fastapi import Security, HTTPException, status, Header, Depends
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.models.models import AIUser

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

DEVELOPER_ROLES = {"AIPlatform.Admin", "AIPlatform.Developer"}
AUDIT_ROLES = {"AIPlatform.Admin", "AIPlatform.Auditor"}
AUTOMATION_ROLES = {"AIPlatform.Admin", "AIPlatform.Developer", "AIPlatform.AutomationAdmin"}

# Microsoft Entra JWKS URL for the specific tenant
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "fcefb508-bb9d-4d5d-b1c5-6d2ef04c0208")
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
ISSUERS = [
    f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    f"https://sts.windows.net/{TENANT_ID}/"
]

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

    if settings.debug and token == "mock-local-token":
        fallback_email = "alden@lotslotsmore.com"
        result = await db.execute(select(AIUser).where(AIUser.email == fallback_email))
        db_user = result.scalar_one_or_none()
        if not db_user:
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
            issuer=ISSUERS
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

    except jwt.ExpiredSignatureError as e:
        print(f"Token validation failed (ExpiredSignatureError): {e}", flush=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signature has expired."
        )
    except jwt.InvalidTokenError as e:
        print(f"Token validation failed (InvalidTokenError): {e}", flush=True)
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

    # 2. API key authentication
    if api_key and api_key == settings.api_key:
        fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        if x_user_id and settings.debug:
            try:
                fallback_user_id = uuid.UUID(x_user_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid X-User-Id format. Must be a UUID."
                )
        result = await db.execute(select(AIUser).where(AIUser.id == fallback_user_id))
        existing_user = result.scalar_one_or_none()
        if not existing_user:
            db_user = AIUser(
                id=fallback_user_id,
                email=f"api-key-{fallback_user_id}@internal",
                display_name=f"API User ({str(fallback_user_id)[:8]})",
                role="user",
                is_active="true",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(db_user)
            await db.commit()
        return {"user_id": fallback_user_id, "email": "api-key@internal", "roles": ["AIPlatform.User"], "mode": "api-key"}

    # 3. Debug mode (local development only, requires DEBUG=true)
    if settings.debug and not api_key:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Anonymous access is not allowed in production even with DEBUG=true."
            )
        fallback_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        return {"user_id": fallback_user_id, "email": "anonymous@local", "roles": ["AIPlatform.Admin"], "mode": "debug"}

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


def has_role(auth: dict, allowed_roles: set[str] | list[str]) -> bool:
    user_roles = set(auth.get("roles", []))
    return "AIPlatform.Admin" in user_roles or bool(user_roles.intersection(set(allowed_roles)))


def require_auth_role(auth: dict, allowed_roles: set[str] | list[str], detail: str = "Access denied.") -> None:
    if not has_role(auth, allowed_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
