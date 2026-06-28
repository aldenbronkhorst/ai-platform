"""Bounded workspace execution for model-generated analysis code.

This is the platform "cloud workspace" surface: scripts run in a temporary
working directory with a clean environment, captured output, and brokered
connector helpers. Connector secrets are never written into the workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import textwrap
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

try:
    import resource
except Exception:  # pragma: no cover - non-Unix fallback
    resource = None  # type: ignore[assignment]


WorkspaceOdooExecutor = Callable[[str, str, list[Any], dict[str, Any]], Awaitable[dict[str, Any]]]

WORKSPACE_TOOL_NAME = "workspace"
WORKSPACE_BACKEND = "local-python"
MAX_CODE_CHARS = int(os.environ.get("WORKSPACE_MAX_CODE_CHARS", "60000"))
MAX_INPUT_FILES = int(os.environ.get("WORKSPACE_MAX_INPUT_FILES", "10"))
MAX_INPUT_FILE_CHARS = int(os.environ.get("WORKSPACE_MAX_INPUT_FILE_CHARS", "100000"))
MAX_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_MAX_TIMEOUT_SECONDS", "120"))
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_DEFAULT_TIMEOUT_SECONDS", "60"))
MAX_OUTPUT_CHARS = int(os.environ.get("WORKSPACE_MAX_OUTPUT_CHARS", "20000"))
MAX_COLLECTED_FILES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILES", "20"))
MAX_COLLECTED_FILE_BYTES = int(os.environ.get("WORKSPACE_MAX_COLLECTED_FILE_BYTES", "1000000"))
MAX_FILE_PREVIEW_CHARS = int(os.environ.get("WORKSPACE_MAX_FILE_PREVIEW_CHARS", "4000"))
CHILD_MEMORY_MB = int(os.environ.get("WORKSPACE_CHILD_MEMORY_MB", "512"))

READ_ONLY_ODOO_METHODS = {
    "check_access_rights",
    "fields_get",
    "get_metadata",
    "name_get",
    "name_search",
    "read",
    "read_group",
    "search",
    "search_count",
    "search_read",
}
READ_ONLY_ODOO_PREFIXES = ("get_", "load_", "web_read", "web_search_read")
INTERNAL_WORKSPACE_FILES = {"main.py", "ai_platform_odoo.py"}


def _truncate_text(value: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n[truncated {len(value) - limit} characters]"


def _safe_relative_path(raw_path: str) -> Path:
    path = Path(str(raw_path or "").strip())
    if not str(path):
        raise ValueError("File path is required.")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe workspace path: {raw_path}")
    if any(part in {"", ".", os.curdir, os.pardir} for part in path.parts):
        raise ValueError(f"Unsafe workspace path: {raw_path}")
    return path


def _workspace_root() -> Path:
    configured = os.environ.get("WORKSPACE_RUNTIME_ROOT")
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "ai-platform-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _clean_env(workdir: Path) -> dict[str, str]:
    return {
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "TEMP": str(workdir),
        "TMP": str(workdir),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _limit_child_process(timeout_seconds: int) -> None:
    if resource is None:
        return
    cpu_limit = max(1, timeout_seconds + 2)
    memory_bytes = max(128, CHILD_MEMORY_MB) * 1024 * 1024
    file_bytes = max(1, MAX_COLLECTED_FILE_BYTES * MAX_COLLECTED_FILES)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
    except Exception:
        pass
    for limit_name, value in (
        ("RLIMIT_AS", memory_bytes),
        ("RLIMIT_DATA", memory_bytes),
        ("RLIMIT_FSIZE", file_bytes),
        ("RLIMIT_NOFILE", 128),
        ("RLIMIT_NPROC", 32),
    ):
        limit = getattr(resource, limit_name, None)
        if limit is None:
            continue
        try:
            resource.setrlimit(limit, (value, value))
        except Exception:
            pass


def _validate_timeout(value: Any) -> int:
    try:
        timeout = int(value or DEFAULT_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def _validate_code(arguments: dict[str, Any]) -> str:
    code = str(arguments.get("code") or "")
    if not code.strip():
        raise ValueError("Workspace requires non-empty Python code.")
    if len(code) > MAX_CODE_CHARS:
        raise ValueError(f"Workspace code is too large; max {MAX_CODE_CHARS} characters.")
    return code


def _write_input_files(workdir: Path, raw_files: Any) -> list[dict[str, Any]]:
    if raw_files in (None, ""):
        return []
    if not isinstance(raw_files, list):
        raise ValueError("files must be a list of {path, content} objects.")
    if len(raw_files) > MAX_INPUT_FILES:
        raise ValueError(f"Too many workspace input files; max {MAX_INPUT_FILES}.")

    written: list[dict[str, Any]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("Each workspace input file must be an object.")
        rel_path = _safe_relative_path(str(item.get("path") or ""))
        content = str(item.get("content") or "")
        if len(content) > MAX_INPUT_FILE_CHARS:
            raise ValueError(f"Workspace input file {rel_path} is too large.")
        target = workdir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append({"path": str(rel_path), "bytes": len(content.encode("utf-8"))})
    return written


def _is_read_only_odoo_method(method: str) -> bool:
    normalized = (method or "").strip()
    if not normalized or normalized.startswith("_"):
        return False
    return normalized in READ_ONLY_ODOO_METHODS or normalized.startswith(READ_ONLY_ODOO_PREFIXES)


class WorkspaceOdooBroker:
    def __init__(self, executor: WorkspaceOdooExecutor | None) -> None:
        self.executor = executor
        self.token = secrets.token_urlsafe(32)
        self.host = "127.0.0.1"
        self.port = 0
        self.calls = 0
        self._server: asyncio.AbstractServer | None = None

    async def __aenter__(self) -> "WorkspaceOdooBroker":
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("Workspace Odoo broker did not start.")
        self.port = int(sockets[0].getsockname()[1])
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=30)
            request = json.loads(raw.decode("utf-8"))
            response = await self._execute_request(request)
        except Exception as exc:
            response = {"error": True, "message": str(exc), "error_type": type(exc).__name__}
        try:
            writer.write(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _execute_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict) or request.get("token") != self.token:
            return {"error": True, "error_type": "workspace_broker_auth_failed", "message": "Invalid workspace broker token."}
        if self.executor is None:
            return {"error": True, "error_type": "odoo_not_available", "message": "Odoo is not connected for this workspace."}

        model = str(request.get("model") or "").strip()
        method = str(request.get("method") or "").strip()
        args = request.get("args") if isinstance(request.get("args"), list) else []
        kwargs = request.get("kwargs") if isinstance(request.get("kwargs"), dict) else {}
        if not model or not method:
            return {"error": True, "error_type": "invalid_odoo_call", "message": "Odoo model and method are required."}
        if not _is_read_only_odoo_method(method):
            return {
                "error": True,
                "error_type": "odoo_method_not_allowed_in_workspace",
                "message": f"Workspace Odoo helper currently allows read-oriented methods only; blocked {model}.{method}.",
            }

        self.calls += 1
        result = await self.executor(model, method, args, kwargs)
        if isinstance(result, dict) and result.get("error"):
            return {
                "error": True,
                "error_type": str(result.get("error_type") or "odoo_error"),
                "message": str(result.get("message") or result.get("error") or "Odoo call failed."),
                "connector_error": result.get("connector_error"),
            }
        if isinstance(result, dict) and "result" in result:
            return {"result": result["result"]}
        return {"result": result}


def _write_odoo_helper(workdir: Path, broker: WorkspaceOdooBroker) -> None:
    helper = f'''
import json
import socket

_HOST = {broker.host!r}
_PORT = {broker.port!r}
_TOKEN = {broker.token!r}


class OdooWorkspaceError(RuntimeError):
    pass


def execute_kw(model, method, args=None, kwargs=None):
    """Call the connected user's Odoo account through the platform broker."""
    payload = {{
        "token": _TOKEN,
        "model": model,
        "method": method,
        "args": args or [],
        "kwargs": kwargs or {{}},
    }}
    with socket.create_connection((_HOST, _PORT), timeout=60) as sock:
        sock.sendall(json.dumps(payload, default=str).encode("utf-8") + b"\\n")
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    response = json.loads(b"".join(chunks).decode("utf-8"))
    if response.get("error"):
        raise OdooWorkspaceError(response.get("message") or response.get("error_type") or "Odoo call failed")
    return response.get("result")


def search(model, domain, **kwargs):
    return execute_kw(model, "search", [domain], kwargs)


def read(model, ids, fields=None, **kwargs):
    options = dict(kwargs)
    if fields is not None:
        options["fields"] = fields
    return execute_kw(model, "read", [ids], options)


def search_read(model, domain, fields=None, **kwargs):
    options = dict(kwargs)
    if fields is not None:
        options["fields"] = fields
    return execute_kw(model, "search_read", [domain], options)


def search_count(model, domain, **kwargs):
    return execute_kw(model, "search_count", [domain], kwargs)
'''
    (workdir / "ai_platform_odoo.py").write_text(textwrap.dedent(helper).strip() + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_text_preview(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _collect_files(workdir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    root = workdir.resolve()
    for path in sorted(workdir.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if not path.is_file() or path.name in INTERNAL_WORKSPACE_FILES:
            continue
        if len(files) >= MAX_COLLECTED_FILES:
            files.append({"truncated": True, "message": "Additional workspace files were omitted from the result."})
            break
        if path.is_symlink():
            continue
        resolved = path.resolve()
        try:
            rel_path = resolved.relative_to(root)
        except ValueError:
            continue
        size = path.stat().st_size
        item: dict[str, Any] = {
            "path": str(rel_path),
            "bytes": size,
            "sha256": _sha256_file(path) if size <= MAX_COLLECTED_FILE_BYTES else None,
        }
        if size <= MAX_FILE_PREVIEW_CHARS:
            data = path.read_bytes()
            if _is_text_preview(data):
                item["preview"] = data.decode("utf-8")
        elif size > MAX_COLLECTED_FILE_BYTES:
            item["omitted_content"] = True
            item["message"] = "File is too large to include in the workspace result."
        files.append(item)
    return files


async def _run_python(workdir: Path, code: str, timeout_seconds: int) -> tuple[int | None, str, str, bool]:
    script = workdir / "main.py"
    script.write_text(code, encoding="utf-8")
    preexec_fn = (lambda: _limit_child_process(timeout_seconds)) if os.name == "posix" else None
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        cwd=str(workdir),
        env=_clean_env(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        preexec_fn=preexec_fn,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return process.returncode, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), False
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return None, stdout_bytes.decode("utf-8", errors="replace"), stderr_bytes.decode("utf-8", errors="replace"), True


async def run_workspace(
    arguments: dict[str, Any],
    *,
    odoo_executor: WorkspaceOdooExecutor | None = None,
) -> dict[str, Any]:
    language = str(arguments.get("language") or "python").strip().lower()
    if language != "python":
        return {
            "status": "failed",
            "error": True,
            "error_type": "unsupported_workspace_language",
            "message": "Workspace currently supports language='python'.",
        }

    try:
        code = _validate_code(arguments)
        timeout_seconds = _validate_timeout(arguments.get("timeout"))
    except ValueError as exc:
        return {"status": "failed", "error": True, "error_type": "invalid_workspace_arguments", "message": str(exc)}

    workspace_id = uuid.uuid4().hex
    workdir = _workspace_root() / workspace_id
    workdir.mkdir(parents=True, exist_ok=False)

    try:
        input_files = _write_input_files(workdir, arguments.get("files"))
        async with WorkspaceOdooBroker(odoo_executor) as broker:
            _write_odoo_helper(workdir, broker)
            exit_code, stdout, stderr, timed_out = await _run_python(workdir, code, timeout_seconds)
            files = _collect_files(workdir)
            status = "failed" if timed_out or exit_code not in (0, None) else "success"
            if timed_out:
                status = "failed"
            return {
                "status": status,
                "backend": WORKSPACE_BACKEND,
                "workspace_id": workspace_id,
                "language": language,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "stdout": _truncate_text(stdout),
                "stderr": _truncate_text(stderr),
                "files": files,
                "input_files": input_files,
                "odoo_calls": broker.calls,
                "helper_modules": ["ai_platform_odoo"],
                "error": bool(status == "failed"),
                "message": "Workspace execution failed." if status == "failed" else "Workspace execution completed.",
            }
    except ValueError as exc:
        return {"status": "failed", "error": True, "error_type": "invalid_workspace_arguments", "message": str(exc)}
    finally:
        if os.environ.get("WORKSPACE_KEEP_RUN_DIRS", "").lower() not in {"1", "true", "yes"}:
            shutil.rmtree(workdir, ignore_errors=True)
