import os
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from app.core.security import api_key_auth
from app.core.database import get_db
from app.models.models import AIConnectedAccount
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

router = APIRouter(prefix="/connected-accounts", tags=["connected-accounts"])

ODOO_CONNECTOR_URL = os.environ.get("ODOO_CONNECTOR_URL", "")
ODOO_CONNECTOR_KEY = os.environ.get("ODOO_CONNECTOR_API_KEY", "")


class OdooConnectRequest(BaseModel):
    url: str = Field(..., description="Odoo instance URL")
    db: str = Field(..., description="Odoo database name")
    username: str = Field(..., description="Odoo username")
    api_key: str = Field(..., description="Odoo API key or password")


class OdooConnectResponse(BaseModel):
    status: str
    account_id: str | None = None
    message: str


@router.post("/odoo", response_model=OdooConnectResponse)
async def connect_odoo(
    req: OdooConnectRequest,
    db: AsyncSession = Depends(get_db),
    auth: dict = Depends(api_key_auth),
):
    """Connect a user's Odoo account. Validates credentials and stores API key in Key Vault."""
    user_id = auth.get("user_id")

    # 1. Validate credentials via Odoo Connector
    import httpx
    headers = {"X-Internal-API-Key": ODOO_CONNECTOR_KEY, "Content-Type": "application/json"}
    payload = {
        "credentials": {
            "url": req.url,
            "db": req.db,
            "username": req.username,
            "api_key": req.api_key,
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ODOO_CONNECTOR_URL.rstrip('/')}/execute-kw/",
                json={**payload, "model": "res.users", "method": "search", "args": [[]]},
                headers=headers,
            )
        if response.status_code >= 400:
            return OdooConnectResponse(
                status="error",
                message=f"Odoo authentication failed: {response.text}",
            )
    except Exception as e:
        return OdooConnectResponse(
            status="error",
            message=f"Could not reach Odoo Connector: {e}",
        )

    # 2. Store API key in Key Vault
    secret_name = f"odoo-api-key-{str(user_id).replace('-', '')}"
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        kv_uri = os.environ.get("KEY_VAULT_URI", "")
        if kv_uri:
            credential = DefaultAzureCredential()
            kv_client = SecretClient(vault_url=kv_uri, credential=credential)
            kv_client.set_secret(secret_name, req.api_key)
        else:
            secret_name = ""
    except Exception as e:
        return OdooConnectResponse(
            status="error",
            message=f"Failed to store credentials in Key Vault: {e}",
        )

    # 3. Upsert connected account record
    result = await db.execute(
        select(AIConnectedAccount).where(
            AIConnectedAccount.user_id == str(user_id),
            AIConnectedAccount.provider == "odoo",
        )
    )
    account = result.scalar_one_or_none()

    if account:
        account.provider_username = req.username
        account.secret_reference = secret_name
        account.status = "active"
        account.last_verified_at = datetime.utcnow()
    else:
        account = AIConnectedAccount(
            user_id=user_id,
            provider="odoo",
            provider_username=req.username,
            secret_reference=secret_name,
            status="active",
            last_verified_at=datetime.utcnow(),
        )
        db.add(account)

    await db.commit()
    await db.refresh(account)

    # 4. Audit
    audit_svc = AuditService(db)
    await audit_svc.log_event(AIAuditEventCreate(
        action_type="create",
        target_system="ai-platform",
        target_model="ai_connected_accounts",
        target_record_id=str(account.id),
        actor_user_id=user_id,
        input_summary=f"Connected Odoo account for user {user_id}",
        risk_level="medium",
        status="success",
    ))

    return OdooConnectResponse(
        status="success",
        account_id=str(account.id),
        message="Odoo account connected successfully.",
    )
