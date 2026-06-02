"""Shared command execution service for azure_cli and github_cli connectors.

Provides secure command execution with timeout, output limits, secret redaction,
structured error classification, command allowlist, and user token injection.
"""
import asyncio
import logging
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

# Command allowlist — only these commands/binary prefixes are permitted
ALLOWED_COMMAND_PREFIXES: list[str] = [
    "az ",
    "gh ",
    "git ",
    "jq ",
    "rg ",
    "which ",
]


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
    # Validate against command allowlist
    cmd_stripped = command.strip()
    if not any(cmd_stripped.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES):
        logger.warning("Blocked command not in allowlist: %.100s", command)
        return CommandResult(error=f"Command not allowed: {cmd_stripped.split()[0] if cmd_stripped else 'empty'}", exit_code=126)

    try:
        args = shlex.split(command)
    except ValueError as e:
        return CommandResult(error=f"Invalid command: {e}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
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
