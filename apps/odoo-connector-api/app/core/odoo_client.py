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


class OdooJson2Unavailable(OdooError):
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


def compact_odoo_json2_error(error: Any) -> str:
    if isinstance(error, dict):
        for key in ("debug", "message", "name"):
            compacted = compact_odoo_rpc_error(error.get(key))
            if compacted != "Odoo returned an error.":
                return compacted
    return compact_odoo_rpc_error(error)


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


def _is_int_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value)


def _json2_payload_from_execute_kw(method: str, args: list[Any] | None, kwargs: dict[str, Any] | None) -> dict[str, Any]:
    """Translate common execute_kw call shapes to Odoo 19 JSON-2 named parameters."""
    payload = dict(kwargs or {})
    positional = list(args or [])
    if not positional:
        return payload

    def require_arg_count(max_count: int) -> None:
        if len(positional) > max_count:
            raise OdooJson2Unavailable(
                f"Odoo JSON-2 cannot infer parameter names for {method} with {len(positional)} positional arguments."
            )

    if method in {"search", "search_read", "search_count"}:
        require_arg_count(2 if method == "search_read" else 1)
        payload.setdefault("domain", positional[0] or [])
        if method == "search_read" and len(positional) > 1:
            payload.setdefault("fields", positional[1])
        return payload

    if method == "read":
        require_arg_count(2)
        payload.setdefault("ids", positional[0])
        if len(positional) > 1:
            payload.setdefault("fields", positional[1])
        return payload

    if method == "fields_get":
        require_arg_count(1)
        payload.setdefault("allfields", positional[0])
        return payload

    if method == "write":
        require_arg_count(2)
        payload.setdefault("ids", positional[0])
        if len(positional) > 1:
            payload.setdefault("vals", positional[1])
        return payload

    if method == "unlink":
        require_arg_count(1)
        payload.setdefault("ids", positional[0])
        return payload

    if method == "create":
        require_arg_count(1)
        payload.setdefault("vals_list", positional[0])
        return payload

    if _is_int_list(positional[0]) and (
        method.startswith(("action_", "button_", "message_"))
        or method in {"copy", "name_get", "name_search", "toggle_active"}
    ):
        require_arg_count(1)
        payload.setdefault("ids", positional[0])
        return payload

    raise OdooJson2Unavailable(
        f"Odoo JSON-2 cannot infer parameter names for {method}; use json2_payload or a legacy RPC transport."
    )


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
        self.json2_url = urljoin(base_url, "json/2/")
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
            message = compact_odoo_jsonrpc_error(data["error"])
            raise OdooError(f"Odoo JSON-RPC error: {message}")
        return data.get("result")

    def execute_kw_json2(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        json2_payload: dict[str, Any] | None = None,
    ) -> Any:
        self.last_transport = "json2"
        payload = dict(json2_payload) if json2_payload is not None else _json2_payload_from_execute_kw(method, args, kwargs)
        headers = {
            "Authorization": f"bearer {self.credentials.password_or_api_key}",
            "X-Odoo-Database": self.credentials.db,
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "ai-platform-odoo-connector",
        }
        with httpx.Client(verify=self.ssl_verify, timeout=self.timeout) as client:
            response = client.post(
                urljoin(self.json2_url, f"{model}/{method}"),
                headers=headers,
                json=payload,
            )
        if response.status_code in {404, 405, 501}:
            raise OdooJson2Unavailable(f"Odoo JSON-2 endpoint unavailable: HTTP {response.status_code}")
        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except Exception:
                error_payload = response.text
            message = compact_odoo_json2_error(error_payload)
            raise OdooError(f"Odoo JSON-2 {model}.{method} failed: {message}")
        return response.json()

    def call_with_transport(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        json2_payload: dict[str, Any] | None = None,
    ) -> Any:
        if self.transport == "xmlrpc":
            return self.execute_kw_xmlrpc(model, method, args, kwargs)
        if self.transport == "jsonrpc":
            return self.execute_kw_jsonrpc(model, method, args, kwargs)
        if self.transport == "json2":
            return self.execute_kw_json2(model, method, args, kwargs, json2_payload=json2_payload)
        if self.transport == "auto":
            try:
                return self.execute_kw_json2(model, method, args, kwargs, json2_payload=json2_payload)
            except OdooJson2Unavailable as json2_error:
                logger.debug("Falling back from Odoo JSON-2 to JSON-RPC: %s", json2_error)
                return self.execute_kw_jsonrpc(model, method, args, kwargs)
        return self.execute_kw_xmlrpc(model, method, args, kwargs)

    def execute_kw(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
        return self.call_with_transport(model, method, args=args, kwargs=kwargs)
