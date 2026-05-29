import json
import ssl
import xmlrpc.client
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings


SAFE_TRANSPORT_FALLBACK_METHODS = {
    "fields_get",
    "fields_view_get",
    "get_views",
    "search",
    "read",
    "search_read",
    "search_count",
    "read_group",
    "name_get",
    "name_search",
    "default_get",
    "web_read",
    "web_search_read",
    "check_access_rights",
    "check_access_rule",
}


class OdooError(Exception):
    pass


class OdooAuthError(OdooError):
    pass


class OdooPermissionError(OdooError):
    pass


@dataclass(frozen=True)
class OdooCredentials:
    url: str
    db: str
    username: str
    password_or_api_key: str


def clean_display_value(value: Any, include_ids: bool = False) -> Any:
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[0], int) and isinstance(value[1], str):
            return {"id": value[0], "name": value[1]} if include_ids else value[1]
        return [clean_display_value(item, include_ids=include_ids) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "id" and not include_ids:
                continue
            cleaned[key] = clean_display_value(item, include_ids=include_ids)
        return cleaned
    return value


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, *, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host: Any):
        connection = super().make_connection(host)
        try:
            connection.timeout = self._timeout
        except Exception:
            pass
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, *, timeout: float, context: ssl.SSLContext | None = None) -> None:
        super().__init__(context=context)
        self._timeout = timeout

    def make_connection(self, host: Any):
        connection = super().make_connection(host)
        try:
            connection.timeout = self._timeout
        except Exception:
            pass
        return connection


def _xmlrpc_transport_for(url: str, context: ssl.SSLContext, timeout: float) -> xmlrpc.client.Transport:
    if urlparse(url).scheme.lower() == "https":
        return _TimeoutSafeTransport(timeout=timeout, context=context)
    return _TimeoutTransport(timeout=timeout)


class OdooClient:
    def __init__(self, credentials: OdooCredentials, transport: str = "auto", timeout: float = 120.0, ssl_verify: bool = True) -> None:
        self.credentials = credentials
        self.transport = transport
        self.timeout = timeout
        self.ssl_verify = ssl_verify
        self._uid: int | None = None

        base_url = credentials.url.rstrip("/") + "/"
        self.common_url = urljoin(base_url, "xmlrpc/2/common")
        self.object_url = urljoin(base_url, "xmlrpc/2/object")

        context = ssl.create_default_context()
        if not ssl_verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        self.common = xmlrpc.client.ServerProxy(
            self.common_url,
            transport=_xmlrpc_transport_for(self.common_url, context, timeout),
            allow_none=True,
        )
        self.models = xmlrpc.client.ServerProxy(
            self.object_url,
            transport=_xmlrpc_transport_for(self.object_url, context, timeout),
            allow_none=True,
        )

    def authenticate(self) -> int:
        if self._uid:
            return self._uid
        uid = self.common.authenticate(
            self.credentials.db,
            self.credentials.username,
            self.credentials.password_or_api_key,
            {},
        )
        if not uid:
            raise OdooAuthError("Odoo authentication failed for the linked user.")
        self._uid = int(uid)
        return self._uid

    def execute_kw_xmlrpc(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        uid = self.authenticate()
        return self.models.execute_kw(
            self.credentials.db,
            uid,
            self.credentials.password_or_api_key,
            model,
            method,
            args or [],
            kwargs or {},
        )

    def execute_kw_jsonrpc(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        uid = self.authenticate()
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "object",
                "method": "execute_kw",
                "args": [
                    self.credentials.db,
                    uid,
                    self.credentials.password_or_api_key,
                    model,
                    method,
                    args or [],
                    kwargs or {},
                ],
            },
            "id": 1,
        }
        with httpx.Client(verify=self.ssl_verify, timeout=self.timeout) as client:
            response = client.post(
                urljoin(self.credentials.url.rstrip("/") + "/", "jsonrpc"),
                headers={"Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise OdooError(f"Odoo JSON-RPC error: {json.dumps(data['error'], default=str)[:2000]}")
        return data.get("result")

    def call_with_transport(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        if self.transport == "xmlrpc":
            return self.execute_kw_xmlrpc(model, method, args, kwargs)
        if self.transport == "jsonrpc":
            return self.execute_kw_jsonrpc(model, method, args, kwargs)
        if self.transport == "auto":
            if method not in SAFE_TRANSPORT_FALLBACK_METHODS:
                return self.execute_kw_xmlrpc(model, method, args, kwargs)
            try:
                return self.execute_kw_jsonrpc(model, method, args, kwargs)
            except Exception as jsonrpc_error:
                try:
                    return self.execute_kw_xmlrpc(model, method, args, kwargs)
                except Exception as xmlrpc_error:
                    raise OdooError(
                        f"Both Odoo API transports failed. JSON-RPC: {jsonrpc_error}; XML-RPC: {xmlrpc_error}"
                    ) from xmlrpc_error
        return self.execute_kw_xmlrpc(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        return self.call_with_transport(model, method, args=args, kwargs=kwargs)

    def ping(self) -> dict[str, Any]:
        uid = self.authenticate()
        return {"ok": True, "uid": uid, "db": self.credentials.db, "url": self.credentials.url}

    def search_read(self, model: str, domain: list[Any] | None = None, fields: list[str] | None = None, limit: int = 10, offset: int = 0, order: str | None = None, include_ids: bool = False) -> list[dict[str, Any]]:
        call_kwargs: dict[str, Any] = {}
        if fields:
            call_kwargs["fields"] = fields
        if limit is not None:
            call_kwargs["limit"] = limit
        if offset:
            call_kwargs["offset"] = offset
        if order:
            call_kwargs["order"] = order
        records = self.call_with_transport(model, "search_read", args=[domain or []], kwargs=call_kwargs)
        return clean_display_value(records, include_ids=include_ids)

    def search_count(self, model: str, domain: list[Any] | None = None) -> int:
        return int(self.call_with_transport(model, "search_count", args=[domain or []], kwargs={}))

    def read(self, model: str, ids: list[int], fields: list[str] | None = None, include_ids: bool = True) -> list[dict[str, Any]]:
        if not ids:
            return []
        args: list[Any] = [ids]
        if fields:
            args.append(fields)
        result = self.call_with_transport(model, "read", args=args, kwargs={})
        return clean_display_value(result, include_ids=include_ids)

    def fields_get(self, model: str, fields: list[str] | None = None, attributes: list[str] | None = None) -> dict[str, Any]:
        args: list[Any] = []
        if fields:
            args.append(fields)
        kwargs: dict[str, Any] = {}
        if attributes:
            kwargs["attributes"] = attributes
        field_errors: dict[str, str] = {}
        try:
            fields_info = self.call_with_transport(model, "fields_get", args=args, kwargs=kwargs)
        except Exception as error:
            if not fields:
                raise
            fields_info = {}
            field_errors["__batch__"] = str(error)
            for field_name in fields:
                try:
                    field_info = self.call_with_transport(model, "fields_get", args=[[field_name]], kwargs=kwargs)
                    if isinstance(field_info, dict):
                        fields_info.update(field_info)
                except Exception as field_error:
                    field_errors[field_name] = str(field_error)
        result: dict[str, Any] = {"model": model, "fields": clean_display_value(fields_info, include_ids=True)}
        if field_errors:
            result["field_errors"] = field_errors
            result["partial"] = True
        return result
