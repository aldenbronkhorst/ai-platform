from fastapi import APIRouter, Depends

from app.core.guidance import connector_manifest, guidance_payload
from app.core.security import internal_api_key_auth

router = APIRouter()


@router.get("/guidance")
def get_guidance(_auth: dict = Depends(internal_api_key_auth)):
    return guidance_payload()


@router.get("/manifest")
def get_manifest(_auth: dict = Depends(internal_api_key_auth)):
    return connector_manifest()
