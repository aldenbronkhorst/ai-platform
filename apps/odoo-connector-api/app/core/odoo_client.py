import logging
import re
import ssl
import xmlrpc.client
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


logger = logging.getLogger(__name__)


class OdooError(Exception):
    pass


class OdooAuthError(OdooError):
    pass


class OdooJsonRpcUnavailable(OdooError):
    pass


ODOO_ERROR_CAUSE_RE = re.compile(
    r"(?m)^(?P<cause>(?:psycopg2\.errors\.[\w]+|odoo\.exceptions\.[\w]+|"
    r"ValidationError|UserError|ValueError|TypeError|KeyError):[^\n]*)"
)
MAX_ODOO_ERROR_CHARS = 1200


def compact_odoo_rpc_error(message: Any) -> str:
    """Return a concise, safe error message from Odoo RPC fault text."""
    text = str(message or "").strip()
    if not text:
        return "Odoo returned an error."

    if "Traceback" in text:
        matches = list(ODOO_ERROR_CAUSE_RE.finditer(text))
        if matches:
            text = matches[-1].group("cause").strip()
        else:
            text = "Odoo returned a server traceback while processing the request."

    text = text.replace("\\n", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if len(text) > MAX_ODOO_ERROR_CHARS:
        return text[:MAX_ODOO_ERROR_CHARS].rstrip() + "..."
    return text


def compact_odoo_jsonrpc_error(error: dict[str, Any]) -> str:
    data = error.get("data")
    if isinstance(data, dict):
        for key in ("debug", "message"):
            compacted = compact_odoo_rpc_error(data.get(key))
            if compacted != "Odoo returned an error.":
                return compacted
        name = data.get("name")
        if name:
            return compact_odoo_rpc_error(name)

    return compact_odoo_rpc_error(error.get("message"))


@dataclass(frozen=True)
class OdooCredentials:
    url: str
    db: str
    username: str
    password_or_api_key: str


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
        self.last_transport: str | None = None

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
        logger.info(
            "OdooClient connecting to Odoo backend url=%s db=%s username=%s transport=%s",
            self.credentials.url,
            self.credentials.db,
            self.credentials.username,
            self.transport,
        )
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

    def _post_jsonrpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(verify=self.ssl_verify, timeout=self.timeout) as client:
            response = client.post(
                urljoin(self.credentials.url.rstrip("/") + "/", "jsonrpc"),
                headers={"Content-Type": "application/json"},
                json=payload,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OdooJsonRpcUnavailable(f"Odoo JSON-RPC endpoint unavailable: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            content_type = response.headers.get("content-type") or "unknown content type"
            raise OdooJsonRpcUnavailable(
                f"Odoo JSON-RPC returned a non-JSON response: HTTP {response.status_code} ({content_type})"
            ) from exc
        if not isinstance(data, dict):
            raise OdooJsonRpcUnavailable(f"Odoo JSON-RPC returned an unexpected response type: {type(data).__name__}")
        return data

    def authenticate_jsonrpc(self) -> int:
        if self._uid:
            return self._uid
        logger.info(
            "OdooClient connecting to Odoo backend url=%s db=%s username=%s transport=jsonrpc",
            self.credentials.url,
            self.credentials.db,
            self.credentials.username,
        )
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "service": "common",
                "method": "authenticate",
                "args": [
                    self.credentials.db,
                    self.credentials.username,
                    self.credentials.password_or_api_key,
                    {},
                ],
            },
            "id": 1,
        }
        data = self._post_jsonrpc(payload)
        if data.get("error"):
            message = compact_odoo_jsonrpc_error(data["error"])
            raise OdooAuthError(f"Odoo JSON-RPC authentication failed: {message}")
        uid = data.get("result")
        if not uid:
            raise OdooAuthError("Odoo authentication failed for the linked user.")
        self._uid = int(uid)
        return self._uid

    def execute_kw_xmlrpc(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        self.last_transport = "xmlrpc"
        uid = self.authenticate()
        try:
            return self.models.execute_kw(
                self.credentials.db,
                uid,
                self.credentials.password_or_api_key,
                model,
                method,
                args or [],
                kwargs or {},
            )
        except xmlrpc.client.Fault as exc:
            message = compact_odoo_rpc_error(exc.faultString)
            raise OdooError(f"Odoo {model}.{method} failed: {message}") from exc

    def execute_kw_jsonrpc(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        self.last_transport = "jsonrpc"
        uid = self.authenticate_jsonrpc()
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
        data = self._post_jsonrpc(payload)
        if data.get("error"):
            message = compact_odoo_jsonrpc_error(data["error"])
            raise OdooError(f"Odoo JSON-RPC error: {message}")
        return data.get("result")

    def call_with_transport(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        if self.transport == "xmlrpc":
            return self.execute_kw_xmlrpc(model, method, args, kwargs)
        if self.transport == "jsonrpc":
            return self.execute_kw_jsonrpc(model, method, args, kwargs)
        if self.transport == "auto":
            return self.execute_kw_jsonrpc(model, method, args, kwargs)
        return self.execute_kw_xmlrpc(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        return self.call_with_transport(model, method, args=args, kwargs=kwargs)
