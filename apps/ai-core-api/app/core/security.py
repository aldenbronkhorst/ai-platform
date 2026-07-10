import hmac
import os
import sys
import jwt
import uuid
from datetime import datetime, timezone
from fastapi import Security, HTTPException, status, Header, Depends
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.models import AIUser

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)

# Microsoft Entra JWKS URL for the specific tenant
TENANT_ID = os.environ.get("ENTRA_TENANT_ID", "03af606c-d85a-48ff-ad4b-a5a8895a6d98")
CLIENT_ID = os.environ.get("ENTRA_CLIENT_ID", "fcefb508-bb9d-4d5d-b1c5-6d2ef04c0208")
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"
ISSUERS = [
    f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
    f"https://sts.windows.net/{TENANT_ID}/"
]

# PyJWKClient manages caching of public keys natively.
jwk_client = jwt.PyJWKClient(JWKS_URL)


def _running_under_pytest() -> bool:
    """True only inside the pytest harness.

    pytest is a dev-only dependency and is never installed in the production
    image, so this is always False in a deployed environment even if APP_ENV
    is misconfigured to "test". It gates the unauthenticated test-mode path so
    that path can never grant access outside the test suite.
    """
    return "pytest" in sys.modules


def _allowed_app_roles() -> set[str]:
    """Entra app roles permitted to use the platform, lower-cased.

    Override with AI_PLATFORM_APP_ROLES (comma-separated) when the app
    registration exposes different role values.
    """
    raw = os.environ.get("AI_PLATFORM_APP_ROLES", "AIPlatform.User,AIPlatform.Admin")
    return {role.strip().lower() for role in raw.split(",") if role.strip()}


def _authorized_by_app_role(token_roles: list | None) -> bool:
    """Whether the token carries a recognized app role.

    Enforced only when REQUIRE_APP_ROLE=true, so existing deployments keep
    working until app roles are assigned in Entra; then enabling it stops open
    auto-provisioning of any tenant principal.
    """
    if not get_settings().require_app_role:
        return True
    assigned = {str(role).strip().lower() for role in (token_roles or [])}
    return bool(assigned & _allowed_app_roles())


async def validate_entra_jwt(token: str, db: AsyncSession) -> dict:
    """Validates the Entra ID access token (signature, audience, issuer, expiration, scopes).

    Resolves the matching database user based on email or Entra Object ID (oid).
    """
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

        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token is missing preferred_username/email claim."
            )

        # Require a recognized app role before allowing/provisioning the user.
        # No-op unless REQUIRE_APP_ROLE=true; when on, closes open
        # auto-provisioning of any tenant principal that can obtain a token.
        if not _authorized_by_app_role(payload.get("roles")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account has no AI Platform app role assigned."
            )

        roles = payload.get("roles") or ["AIPlatform.User"]  # default only when enforcement is off

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
            "db_role": db_user.role,
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
) -> dict:
    """Production-ready unified authentication dependency.

    Accepts Entra Bearer Token for users and API key auth for internal calls.
    """
    settings = get_settings()

    if bearer and bearer.credentials:
        async with AsyncSessionLocal() as db:
            return await validate_entra_jwt(bearer.credentials, db)

    if settings.app_env == "test" and _running_under_pytest():
        try:
            test_user_id = uuid.UUID(x_user_id) if x_user_id else uuid.UUID("00000000-0000-0000-0000-000000000001")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-User-Id format. Must be a UUID."
            )
        return {
            "user_id": test_user_id,
            "email": f"test-{test_user_id}@local",
            "roles": ["AIPlatform.Admin", "AIPlatform.User"],
            "mode": "test",
        }

    # Compare as UTF-8 bytes: hmac.compare_digest raises TypeError on non-ASCII
    # str operands, and the X-API-Key header is latin-1-decoded, so a non-ASCII
    # header value would otherwise crash the auth path (500) instead of falling
    # through to 401. Byte comparison is unrestricted and still constant-time.
    if settings.api_key and api_key and hmac.compare_digest(api_key.encode("utf-8"), settings.api_key.encode("utf-8")):
        api_user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AIUser).where(AIUser.id == api_user_id))
            existing_user = result.scalar_one_or_none()
            if not existing_user:
                db_user = AIUser(
                    id=api_user_id,
                    email=f"api-key-{api_user_id}@internal",
                    display_name=f"API User ({str(api_user_id)[:8]})",
                    role="user",
                    is_active="true",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                db.add(db_user)
                await db.commit()
        return {
            "user_id": api_user_id,
            "email": "api-key@internal",
            "roles": ["AIPlatform.User"],
            "db_role": existing_user.role if existing_user else "user",
            "mode": "api-key",
        }

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid authentication credentials (use Bearer JWT)."
    )
