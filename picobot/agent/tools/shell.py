"""Shell execution tool with security hardening."""

import asyncio
import os
import re
import shlex
from pathlib import Path
from typing import Any

from picobot.agent.tools.base import Tool


SAFE_COMMANDS_ALLOWLIST = {
    # File reading
    "cat", "head", "tail", "less", "more", "grep", "rg", "ag", "find",
    # File info
    "ls", "dir", "stat", "file", "wc", "du", "df", "tree",
    # Text processing
    "awk", "sed", "cut", "sort", "uniq", "tr", "jq", "yq",
    # System info
    "ps", "top", "htop", "free", "uptime", "uname", "hostname", "whoami", "id",
    "env", "printenv", "which", "whereis", "type", "command", "hash",
    # Git
    "git", "gh",
    # Version control
    "svn", "hg",
    # Network
    "curl", "wget", "ping", "netstat", "ss", "ip", "ifconfig", "dig", "nslookup",
    "traceroute", "mtr", "nc", "ssh", "scp", "rsync",
    # Package managers
    "pip", "pip3", "npm", "yarn", "pnpm", "brew", "apt", "apt-get", "yum", "dnf",
    "cargo", "go", "gradle", "mvn", "make", "cmake",
    # Development tools
    "python", "python3", "node", "ruby", "perl", "php", "java", "javac",
    "clang", "gcc", "g++", "rustc", "zig",
    "docker", "docker-compose", "podman",
    # Editors (read-only or safe)
    "vi", "vim", "nano", "emacs", "code", "subl",
    # Archives
    "tar", "gzip", "gunzip", "zip", "unzip", "bzip2", "xz",
    # Misc
    "date", "sleep", "echo", "printf", "yes", "seq", "seq",
    "base64", "md5sum", "sha256sum", "sha1sum",
    "xargs", "parallel", "timeout", "time",
}


class ExecTool(Tool):
    """Tool to execute shell commands with security hardening."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        enforce_allowlist: bool = True,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"(?:^|[;&|]\s*)format\b",       # format (as standalone command only)
            r"\b(mkfs|diskpart)\b",          # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
            r"\bsudo\s+su\b",                # privilege escalation
            r"\bchmod\s+777\b",              # overly permissive perms
            r"\beval\b",                     # eval is dangerous
            r"\bsource\s+/etc/environment",  # env injection
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self.enforce_allowlist = enforce_allowlist

    @property
    def name(self) -> str:
        return "exec"

    _MAX_TIMEOUT = 600
    _MAX_OUTPUT = 10_000

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. For file operations (create, modify, delete files/folders), use the dax tool with create_run action instead. Use exec only for read-only commands, checking status, or running scripts."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Timeout in seconds. Increase for long-running commands "
                        "like compilation or installation (default 60, max 600)."
                    ),
                    "minimum": 1,
                    "maximum": 600,
                },
            },
            "required": ["command"],
        }

    FILE_OP_PATTERNS = [
        r"\bmkdir\b", r"\bmkdir\s", r"\brmdir\b", r"\brm\s", r"\brm\s+",
        r"\brmdir\b", r"\bdel\b", r"\brmdir\s", r"\brm -", r"\brmdir -",
        r"\bcp\s", r"\bmv\s", r"\bcp\s", r"\bchmod\b", r"\bchown\b",
        r"\btouch\b", r"\bmkfile\b",
    ]

    async def execute(
        self, command: str, working_dir: str | None = None,
        timeout: int | None = None, **kwargs: Any,
    ) -> str:
        import re
        
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error
        
        # Detect file operations that should go through DAX
        for pattern in self.FILE_OP_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return ("⚠️ File operation detected. For safety, file operations "
                        "(create, modify, delete files/folders) must go through DAX approval.\n\n"
                        "Please use the `dax` tool with `action: create_run` instead, "
                        "or confirm you want to proceed with exec (file changes will bypass approval).")

        effective_timeout = min(timeout or self.timeout, self._MAX_TIMEOUT)

        env = os.environ.copy()
        if self.path_append:
            env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=effective_timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                return f"Error: Command timed out after {effective_timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Head + tail truncation to preserve both start and end of output
            max_len = self._MAX_OUTPUT
            if len(result) > max_len:
                half = max_len // 2
                result = (
                    result[:half]
                    + f"\n\n... ({len(result) - max_len:,} chars truncated) ...\n\n"
                    + result[-half:]
                )

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.enforce_allowlist and not self.allow_patterns:
            cmd_base = self._extract_command_base(command)
            if cmd_base and cmd_base not in SAFE_COMMANDS_ALLOWLIST:
                return f"Error: Command '{cmd_base}' not in allowlist. Use file tools or DAX for file operations."

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    p = Path(expanded).expanduser().resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    @staticmethod
    def _extract_command_base(command: str) -> str | None:
        """Extract the base command name from a shell command."""
        try:
            parts = shlex.split(command)
            if not parts:
                return None
            cmd = parts[0]
            return os.path.basename(cmd).lower()
        except Exception:
            cmd = command.strip().split()[0] if command.strip() else ""
            return os.path.basename(cmd).lower() if cmd else None

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)   # Windows: C:\...
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command) # POSIX: /absolute only
        home_paths = re.findall(r"(?:^|[\s|>'\"])(~[^\s\"'>;|<]*)", command) # POSIX/Windows home shortcut: ~
        return win_paths + posix_paths + home_paths
