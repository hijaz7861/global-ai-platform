"""Hardened Docker sandbox runner for disposable, untrusted code checks.

Prerequisites: a trusted, digest-pinned runner image and a rootless Docker daemon.
This runner never pulls images, never attaches the Docker socket to the container,
disables container networking, and exposes only the supplied artifact directory read-only.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import selectors
import subprocess
import time


_PINNED_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool
    duration_seconds: float


class DockerSandboxRunner:
    """Run an allowlisted command in a rootless, no-network, resource-limited container."""

    def __init__(
        self,
        *,
        image: str,
        docker_binary: str = "docker",
        timeout_seconds: int = 120,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        pids_limit: int = 128,
        max_output_bytes: int = 1_048_576,
    ) -> None:
        if not _PINNED_IMAGE.fullmatch(image):
            raise ValueError("Sandbox image must be pinned by a full sha256 digest")
        if not 1 <= timeout_seconds <= 900:
            raise ValueError("Sandbox timeout must be between 1 and 900 seconds")
        if memory_limit not in {"256m", "512m", "1g", "2g"}:
            raise ValueError("Unsupported sandbox memory limit")
        if cpu_limit not in {"0.5", "1.0", "2.0"}:
            raise ValueError("Unsupported sandbox CPU limit")
        if not 16 <= pids_limit <= 512:
            raise ValueError("Sandbox pids limit must be between 16 and 512")
        if not 1024 <= max_output_bytes <= 10_485_760:
            raise ValueError("Output limit must be between 1024 and 10485760 bytes")
        self.image = image
        self.docker_binary = docker_binary
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.max_output_bytes = max_output_bytes

    def _require_rootless_daemon(self) -> None:
        try:
            result = subprocess.run(
                [self.docker_binary, "info", "--format", "{{json .SecurityOptions}}"],
                check=False, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Unable to verify Docker daemon; sandbox execution refused") from exc
        if result.returncode != 0 or "rootless" not in result.stdout.lower():
            raise RuntimeError("Rootless Docker daemon is required; sandbox execution refused")

    def run(self, *, artifact_directory: str | Path, command: list[str]) -> SandboxResult:
        """Execute a command in isolation. Command is argv, never a shell string."""
        artifact = Path(artifact_directory).resolve(strict=True)
        if not artifact.is_dir():
            raise ValueError("Artifact path must be a directory")
        if not command or len(command) > 128:
            raise ValueError("Command must contain between 1 and 128 arguments")
        if any(not isinstance(part, str) or not part or "\x00" in part or len(part) > 4096
               for part in command):
            raise ValueError("Command arguments must be non-empty bounded strings")
        self._require_rootless_daemon()

        docker_command = [
            self.docker_binary, "run", "--rm", "--pull=never",
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit", str(self.pids_limit),
            "--memory", self.memory_limit, "--cpus", self.cpu_limit,
            "--user", "65532:65532",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,uid=65532,gid=65532",
            "--mount", f"type=bind,src={artifact},dst=/workspace,readonly",
            "--workdir", "/workspace", self.image, *command,
        ]
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                docker_command, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, close_fds=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
            )
        except OSError as exc:
            raise RuntimeError("Unable to start sandbox container") from exc

        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        collected = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        timed_out = False
        output_limit_exceeded = False
        deadline = started + self.timeout_seconds

        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    process.kill()
                    break
                events = selector.select(min(remaining, 0.25))
                if process.poll() is not None and not events:
                    # Drain any remaining pipe data before returning.
                    events = selector.select(0)
                    if not events:
                        for key in list(selector.get_map().values()):
                            selector.unregister(key.fileobj)
                        break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > self.max_output_bytes:
                        output_limit_exceeded = True
                        process.kill()
                        break
                    collected[key.data].extend(chunk)
                if output_limit_exceeded:
                    break
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()

        return SandboxResult(
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=collected["stdout"].decode("utf-8", errors="replace"),
            stderr=collected["stderr"].decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            duration_seconds=round(time.monotonic() - started, 3),
        )
