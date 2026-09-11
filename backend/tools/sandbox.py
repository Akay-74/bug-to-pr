"""Docker-based execution sandbox.

Foundation only: one container per validation run, reused across a handful of
exec calls (clone/install/test), non-root, resource-limited, timeout-enforced,
ephemeral workspace. Not a general-purpose container orchestration layer.
"""
from __future__ import annotations

import shlex
import time
import uuid
from dataclasses import dataclass

import docker
from docker.errors import NotFound

# GNU coreutils `timeout` exits 124 when it had to kill the wrapped command.
_TIMEOUT_EXIT_CODE = 124

# Any UID works here: the workspace directory is made world-writable before
# the container starts, and a virtualenv is created inside it, so nothing
# needs root-owned system directories to be writable.
_SANDBOX_UID = "65534:65534"  # nobody:nogroup


@dataclass
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool


class DockerSandbox:
    """A single ephemeral, resource-limited container that can run several
    commands in sequence (e.g. clone, install, test) against one workspace.
    """

    def __init__(
        self,
        image: str,
        cpu_limit: float,
        memory_limit: str,
        workspace: str | None = None,
        network_disabled: bool = True,
        client: docker.DockerClient | None = None,
    ) -> None:
        self.image = image
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.workspace = workspace
        self.network_disabled = network_disabled
        self._client = client or docker.from_env()
        self._container = None

    def __enter__(self) -> "DockerSandbox":
        # No Docker socket, no application secrets, and (by default) no
        # network are ever passed into this container. The workspace bind
        # mount is the only host filesystem exposure, and it is scoped to
        # one ephemeral directory made specifically for this run.
        volumes = {}
        if self.workspace is not None:
            volumes[self.workspace] = {"bind": "/workspace", "mode": "rw"}

        self._container = self._client.containers.run(
            self.image,
            command=["sleep", "infinity"],
            detach=True,
            name=f"bug2pr-sandbox-{uuid.uuid4().hex[:12]}",
            nano_cpus=int(self.cpu_limit * 1e9),
            mem_limit=self.memory_limit,
            network_disabled=self.network_disabled,
            volumes=volumes,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            read_only=False,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._container is None:
            return
        try:
            self._container.kill()
        except Exception:
            pass
        try:
            self._container.remove(force=True)
        except NotFound:
            pass
        self._container = None

    def run(self, command: str, timeout: int, workdir: str = "/workspace", user: str | None = None) -> ExecutionResult:
        """Run `command` (a shell string) inside the container, enforcing
        `timeout` seconds via coreutils `timeout`, and return the result.
        """
        if self._container is None:
            raise RuntimeError("DockerSandbox must be used as a context manager")

        wrapped = f"timeout {int(timeout)}s bash -c {shlex.quote(command)}"
        start = time.monotonic()
        exec_result = self._container.exec_run(
            cmd=["bash", "-lc", wrapped],
            workdir=workdir,
            user=user or _SANDBOX_UID,
            demux=True,
        )
        duration = time.monotonic() - start

        stdout_bytes, stderr_bytes = exec_result.output
        stdout = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr = (stderr_bytes or b"").decode("utf-8", errors="replace")
        exit_code = exec_result.exit_code
        timed_out = exit_code == _TIMEOUT_EXIT_CODE

        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration=duration,
            timed_out=timed_out,
        )


def run(
    command: str,
    workspace: str,
    timeout: int,
    cpu_limit: float,
    memory_limit: str,
    image: str = "python:3.11-slim",
    network_disabled: bool = True,
) -> ExecutionResult:
    """One-shot convenience wrapper: run a single command in a fresh
    container with `workspace` bind-mounted at /workspace.
    """
    client = docker.from_env()
    wrapped = f"timeout {int(timeout)}s bash -c {shlex.quote(command)}"
    start = time.monotonic()
    container = client.containers.run(
        image,
        command=["bash", "-lc", wrapped],
        name=f"bug2pr-sandbox-{uuid.uuid4().hex[:12]}",
        working_dir="/workspace",
        volumes={workspace: {"bind": "/workspace", "mode": "rw"}},
        nano_cpus=int(cpu_limit * 1e9),
        mem_limit=memory_limit,
        network_disabled=network_disabled,
        user=_SANDBOX_UID,
        security_opt=["no-new-privileges"],
        cap_drop=["ALL"],
        detach=True,
    )
    try:
        result = container.wait(timeout=timeout + 30)
        exit_code = result.get("StatusCode", -1)
        duration = time.monotonic() - start
        stdout_bytes = container.logs(stdout=True, stderr=False)
        stderr_bytes = container.logs(stdout=False, stderr=True)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        timed_out = exit_code == _TIMEOUT_EXIT_CODE
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

    return ExecutionResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration=duration, timed_out=timed_out)


def purge_directory(path: str, image: str = "python:3.11-slim") -> bool:
    """Delete everything inside `path` from within a container.

    The sandbox runs as uid 65534, so the directories it creates (a
    virtualenv, __pycache__, *.egg-info) are owned by that uid and the host
    user cannot unlink files inside them. `shutil.rmtree(ignore_errors=True)`
    therefore leaves them behind silently -- measured at 150-350 MB per
    workflow run, which fills /tmp over an evaluation.

    The deletion runs as the same uid that created the files, so it needs no
    added capability: the container still drops ALL of them, has no network
    and mounts nothing but this directory.

    Returns True if the container ran; the caller retries its own rmtree
    either way.
    """
    try:
        client = docker.from_env()
        client.containers.run(
            image,
            command=["bash", "-lc", "rm -rf /workspace/* /workspace/.[!.]* /workspace/..?* || true"],
            volumes={path: {"bind": "/workspace", "mode": "rw"}},
            user=_SANDBOX_UID,
            network_disabled=True,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            remove=True,
        )
        return True
    except Exception:
        # Cleanup is best-effort: a workspace we cannot purge is a disk-space
        # problem, never a reason to fail the run that produced it.
        return False
