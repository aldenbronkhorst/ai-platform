"""Shared command execution service for azure_cli and github_cli connectors.

Provides secure command execution with timeout, output limits, secret redaction,
structured error classification, command allowlist, and user token injection.
"""
import asyncio
import logging
import os
import re
import shlex
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 50000
MAX_STDERR_CHARS = 10000
DEFAULT_TIMEOUT_SECONDS = 60

SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(token|secret|password|key|credential|connection.string|authorization)\s*[=:]\s*\S+'),
    re.compile(r'(?i)(-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----.*?-----END\s+(RSA\s+)?PRIVATE\s+KEY-----)'),
    re.compile(r'(?i)(ghp_|gho_|ghu_|ghs_|ghr_)[\w-]+'),
    re.compile(r'(?i)pat=\S+|token=\S+|password=\S+'),
]

ALLOWED_OPERATIONS: dict[str, set[tuple[str, ...]]] = {
    "az": {
        ("account", "show"),
        ("account", "list"),
        ("group", "show"),
        ("group", "list"),
        ("resource", "show"),
        ("resource", "list"),
        ("containerapp", "show"),
        ("containerapp", "list"),
        ("acr", "repository", "list"),
        ("acr", "repository", "show-tags"),
        ("servicebus", "namespace", "show"),
        ("servicebus", "queue", "show"),
        ("servicebus", "queue", "list"),
        ("storage", "account", "show"),
        ("search", "service", "show"),
    },
    "gh": {
        ("auth", "status"),
        ("repo", "view"),
        ("repo", "list"),
        ("issue", "view"),
        ("issue", "list"),
        ("pr", "view"),
        ("pr", "list"),
        ("pr", "checks"),
        ("run", "view"),
        ("run", "list"),
    },
    "git": {
        ("status",),
        ("log",),
        ("show",),
        ("diff",),
        ("branch",),
        ("remote",),
    },
    "jq": {()},
    "rg": {()},
    "which": {()},
}


def _is_allowed_operation(args: list[str]) -> bool:
    if not args:
        return False
    binary = args[0]
    allowed = ALLOWED_OPERATIONS.get(binary)
    if allowed is None:
        return False
    if () in allowed:
        return True

    op_parts = tuple(part for part in args[1:] if not part.startswith("-"))
    return any(op_parts[:len(prefix)] == prefix for prefix in allowed)


def _redact_output(text: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        text = pattern.sub(lambda m: m.group(0)[:8] + "***REDACTED***", text)
    return text


class CommandResult:
    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0,
                 timed_out: bool = False, error: Optional[str] = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        stdout_redacted = _redact_output(self.stdout)
        stderr_redacted = _redact_output(self.stderr)
        truncated = len(stdout_redacted) > MAX_OUTPUT_CHARS or len(stderr_redacted) > MAX_STDERR_CHARS
        return {
            "stdout": stdout_redacted[:MAX_OUTPUT_CHARS],
            "stderr": stderr_redacted[:MAX_STDERR_CHARS],
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "output_truncated": truncated,
            "stdout_chars": len(stdout_redacted),
            "stderr_chars": len(stderr_redacted),
            "error": self.error,
        }

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.error


async def run_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> CommandResult:
    """Execute a CLI command with timeout, output limits, and allowlist validation."""
    try:
        args = shlex.split(command)
    except ValueError as e:
        return CommandResult(error=f"Invalid command: {e}")

    if not _is_allowed_operation(args):
        logger.warning("Blocked command outside read-only operation policy: %.100s", command)
        return CommandResult(error=f"Command not allowed by read-only operation policy: {args[0] if args else 'empty'}", exit_code=126)

    try:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        return CommandResult(error=f"Command not found: {e}", exit_code=127)
    except PermissionError as e:
        return CommandResult(error=f"Permission denied: {e}", exit_code=126)
    except Exception as e:
        return CommandResult(error=f"Execution failed: {e}", exit_code=1)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CommandResult(timed_out=True, error=f"Command timed out after {timeout}s")

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    return CommandResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode or 0,
    )
