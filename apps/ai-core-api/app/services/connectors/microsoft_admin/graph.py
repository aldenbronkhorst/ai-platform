"""Direct Microsoft Graph tool for the native Microsoft Graph connector."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx

from app.services.connectors.microsoft_admin.constants import (
    GRAPH_AUTO_PAGE_MAX_ITEMS,
    GRAPH_AUTO_PAGE_MAX_PAGES,
    MICROSOFT_GRAPH_BASE_URL,
    MICROSOFT_GRAPH_SCOPE,
)
from app.services.connectors.microsoft_admin.powershell_common import _failed_microsoft_admin_result
from app.services.connectors.microsoft_admin.tokens import _get_fresh_microsoft_admin_token_for_scope

logger = logging.getLogger(__name__)

async def run_ms_graph_tool(arguments: dict[str, Any], user_id: Optional[UUID], timeout: int = 60) -> dict[str, Any]:
    """Execute a direct Microsoft Graph request through the native Microsoft Graph connector."""
    request_id = uuid.uuid4().hex[:16]
    return await _run_microsoft_admin_graph_request(arguments, user_id, request_id=request_id, connector_name="ms_graph")

async def _run_microsoft_admin_graph_request(
    arguments: dict[str, Any],
    user_id: Optional[UUID],
    request_id: str,
    *,
    connector_name: str = "ms_graph",
) -> dict[str, Any]:
    method = str(arguments.get("method") or "GET").strip().upper()
    path = str(arguments.get("path") or "").strip()
    api_version = str(arguments.get("api_version") or "v1.0").strip().strip("/")
    max_pages = _bounded_int(arguments.get("max_pages"), GRAPH_AUTO_PAGE_MAX_PAGES, 1, 100)
    max_items = _bounded_int(arguments.get("max_items"), GRAPH_AUTO_PAGE_MAX_ITEMS, 1, 5000)
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="graph_request",
            message="Unsupported Graph method.",
            connector=connector_name,
        )
    if not path.startswith("/"):
        return _failed_microsoft_admin_result(
            request_id=request_id,
            mode="graph_request",
            message="Graph path must start with '/'.",
            connector=connector_name,
        )

    token_data = await _get_fresh_microsoft_admin_token_for_scope(user_id, MICROSOFT_GRAPH_SCOPE) if user_id else None
    if not token_data or not token_data.get("access_token"):
        return {
            "status": "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "not_connected",
            "message": "Microsoft Graph token is not available. Reconnect Microsoft Graph and ensure tenant consent/user permissions are available.",
            "refresh_error": token_data.get("refresh_error") if token_data else None,
        }

    local_skip = _local_graph_skip(path)
    request_path = local_skip["path"] if local_skip else path
    fetch_max_items = max_items + int(local_skip["skip"]) if local_skip else max_items
    url = f"{MICROSOFT_GRAPH_BASE_URL.rstrip('/')}/{api_version}{request_path}"
    headers = {
        "Authorization": f"Bearer {token_data['access_token']}",
        "Content-Type": "application/json",
    }
    extra_headers = arguments.get("headers")
    if isinstance(extra_headers, dict):
        headers.update({str(k): str(v) for k, v in extra_headers.items()})
    body = arguments.get("body")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response, data = await _request_graph_with_paging(
                client,
                method,
                url,
                headers=headers,
                body=body,
                max_pages=max_pages,
                max_items=fetch_max_items,
            )
        if local_skip and response.status_code < 400:
            data = _apply_local_graph_skip(data, int(local_skip["skip"]))
        error_type, message = _graph_error_details(data, response.status_code)
        return {
            "status": "success" if response.status_code < 400 else "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "method": method,
            "path": path,
            "api_version": api_version,
            "status_code": response.status_code,
            "result": data,
            **({"error_type": error_type} if error_type else {}),
            **({"message": message} if message else {}),
        }
    except Exception as exc:
        logger.warning("Microsoft Graph request failed for request_id=%s: %s", request_id, exc)
        return {
            "status": "failed",
            "connector": connector_name,
            "mode": "graph_request",
            "request_id": request_id,
            "error_type": "graph_request_failed",
            "message": "Microsoft Graph request failed. Check connector logs with this request_id.",
        }


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _local_graph_skip(path: str) -> dict[str, Any] | None:
    """Handle Graph endpoints, like /users, where manual $skip is rejected.

    Microsoft Graph returns @odata.nextLink with a skip token for these
    collections. If the model still asks for $skip, fetch the collection and
    apply the requested skip locally instead of surfacing a noisy failed span.
    """
    parts = urlsplit(path)
    if parts.path.rstrip("/").lower() != "/users":
        return None

    query = parse_qsl(parts.query, keep_blank_values=True)
    skip_value: int | None = None
    kept: list[tuple[str, str]] = []
    for key, value in query:
        if key.lower() == "$skip":
            try:
                skip_value = max(0, int(value))
            except (TypeError, ValueError):
                skip_value = 0
            continue
        kept.append((key, value))

    if skip_value is None:
        return None

    cleaned = urlunsplit(("", "", parts.path, urlencode(kept, doseq=True, safe="$,()"), parts.fragment))
    return {"path": cleaned, "skip": skip_value}


async def _request_graph_with_paging(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any,
    max_pages: int,
    max_items: int,
) -> tuple[httpx.Response, Any]:
    response = await client.request(method, url, headers=headers, json=body if body is not None else None)
    data = _graph_response_data(response)
    if method != "GET" or response.status_code >= 400 or not _is_graph_collection(data):
        return response, data

    first_data = data if isinstance(data, dict) else {}
    collected = list(first_data.get("value") or [])
    next_link = first_data.get("@odata.nextLink")
    pages = 1
    last_response = response

    while next_link and pages < max_pages and len(collected) < max_items:
        last_response = await client.request("GET", str(next_link), headers=headers)
        page_data = _graph_response_data(last_response)
        if last_response.status_code >= 400 or not _is_graph_collection(page_data):
            return last_response, page_data
        collected.extend(list(page_data.get("value") or []))
        next_link = page_data.get("@odata.nextLink")
        pages += 1

    complete = not next_link and len(collected) <= max_items
    combined = dict(first_data)
    combined["value"] = collected[:max_items]
    if not complete and next_link:
        combined["@odata.nextLink"] = next_link
    else:
        combined.pop("@odata.nextLink", None)
    combined["pagination"] = {
        "auto_paged": pages > 1,
        "pages_fetched": pages,
        "returned_count": len(combined["value"]),
        "complete": complete,
    }
    return last_response, combined


def _graph_response_data(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _is_graph_collection(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("value"), list)


def _apply_local_graph_skip(data: Any, skip: int) -> Any:
    if not _is_graph_collection(data):
        return data
    adjusted = dict(data)
    values = list(adjusted.get("value") or [])
    adjusted["value"] = values[skip:]
    pagination = dict(adjusted.get("pagination") or {})
    pagination.update({
        "local_skip_applied": skip,
        "pre_skip_count": len(values),
        "returned_count": len(adjusted["value"]),
    })
    adjusted["pagination"] = pagination
    adjusted["warning"] = (
        "The requested Microsoft Graph endpoint does not support manual $skip. "
        "The connector fetched the collection and applied the skip locally."
    )
    return adjusted


def _graph_error_details(data: Any, status_code: int) -> tuple[str | None, str | None]:
    if status_code < 400:
        return None, None
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "graph_http_error")
            message = str(error.get("message") or f"Microsoft Graph returned HTTP {status_code}.")
            return code, message
    return "graph_http_error", f"Microsoft Graph returned HTTP {status_code}."
