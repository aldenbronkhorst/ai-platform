import logging
from fastapi import APIRouter, Depends
from app.core.security import internal_api_key_auth
from app.core.odoo_client import OdooClient, OdooCredentials
from app.models.schemas import HealthCheckRequest

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_client(creds):
    return OdooClient(
        credentials=OdooCredentials(
            url=creds.url, db=creds.db, username=creds.username,
            password_or_api_key=creds.api_key,
        ),
        transport=creds.transport,
    )


@router.post("/check")
def odoo_health_check(req: HealthCheckRequest, _auth: dict = Depends(internal_api_key_auth)):
    """Check Odoo connection health, authenticated user, database, and version."""
    try:
        client = _get_client(req.credentials)
        uid = client.authenticate()
        version_info = client.call_with_transport("res.users", "search_read", args=[[["id", "=", uid]], ["login", "name"]], kwargs={"limit": 1})
        return {
            "status": "healthy",
            "authenticated": True,
            "user_id": uid,
            "database": req.credentials.db,
            "instance_url": req.credentials.url,
            "version": getattr(client, 'server_version', None),
        }
    except Exception as e:
        return {
            "status": "error",
            "authenticated": False,
            "database": req.credentials.db,
            "instance_url": req.credentials.url,
            "error": str(e),
        }
