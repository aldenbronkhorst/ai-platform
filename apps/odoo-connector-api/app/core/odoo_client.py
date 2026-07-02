import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

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
    r"ValidationError|UserError|ValueError|TypeError|KeyError|AttributeError):[^\n]*)"
)
MAX_ODOO_ERROR_CHARS = 1200
GENERIC_ODOO_TRACEBACK_MESSAGE = "Odoo returned a server traceback while processing the request."
TRANSIENT_HTTP_STATUSES = {502, 503, 504}


def compact_odoo_error_message(message: Any) -> str:
    """Return a concise, safe error message from Odoo RPC fault text."""
    text = str(message or "").strip()
    if not text:
        return "Odoo returned an error."

    if "Traceback" in text:
        matches = list(ODOO_ERROR_CAUSE_RE.finditer(text))
        if matches:
            text = matches[-1].group("cause").strip()
        else:
            text = GENERIC_ODOO_TRACEBACK_MESSAGE

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
        debug = compact_odoo_error_message(data.get("debug"))
        if debug not in {"Odoo returned an error.", GENERIC_ODOO_TRACEBACK_MESSAGE}:
            return debug
        message = compact_odoo_error_message(data.get("message"))
        if message != "Odoo returned an error.":
            return message
        name = data.get("name")
        if name:
            return compact_odoo_error_message(name)

    return compact_odoo_error_message(error.get("message"))


@dataclass(frozen=True)
class OdooCredentials:
    url: str
    db: str
    username: str
    password_or_api_key: str


class OdooClient:
    def __init__(
        self,
        credentials: OdooCredentials,
        timeout: float = 120.0,
        ssl_verify: bool = True,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.4,
    ) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self.ssl_verify = ssl_verify
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._uid: int | None = None

        base_url = credentials.url.rstrip("/") + "/"
        self.jsonrpc_url = urljoin(base_url, "jsonrpc")

    def _retry_delay(self, attempt_index: int) -> None:
        if self.retry_backoff_seconds <= 0:
            return
        time.sleep(self.retry_backoff_seconds * attempt_index)

    def _should_retry_http_error(self, exc: httpx.HTTPError) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in TRANSIENT_HTTP_STATUSES
        return isinstance(exc, httpx.TransportError)

    def _post_jsonrpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_http_error: httpx.HTTPError | None = None
        response: httpx.Response | None = None
        with httpx.Client(verify=self.ssl_verify, timeout=self.timeout) as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = client.post(
                        self.jsonrpc_url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError as exc:
                    last_http_error = exc
                    if attempt >= self.max_attempts or not self._should_retry_http_error(exc):
                        raise OdooJsonRpcUnavailable(f"Odoo JSON-RPC endpoint unavailable: {exc}") from exc
                    logger.info(
                        "Retrying Odoo JSON-RPC after transient error: attempt=%s status=%s url=%s",
                        attempt + 1,
                        getattr(getattr(exc, "response", None), "status_code", "transport_error"),
                        self.jsonrpc_url,
                    )
                    self._retry_delay(attempt)
            else:
                raise OdooJsonRpcUnavailable(f"Odoo JSON-RPC endpoint unavailable: {last_http_error}")
        if response is None:
            raise OdooJsonRpcUnavailable("Odoo JSON-RPC endpoint unavailable: no response received")
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

    def authenticate(self) -> int:
        if self._uid:
            return self._uid
        logger.info(
            "OdooClient connecting to Odoo backend url=%s db=%s username=%s via JSON-RPC",
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

    def execute_kw(self, model: str, method: str, args: list[Any] | None = None, kwargs: dict[str, Any] | None = None) -> Any:
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
        data = self._post_jsonrpc(payload)
        if data.get("error"):
            message = compact_odoo_jsonrpc_error(data["error"])
            raise OdooError(f"Odoo JSON-RPC error: {message}")
        return data.get("result")
