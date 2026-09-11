from backend.tools.sandbox import DockerSandbox, run


def test_run_captures_stdout_and_exit_code(tmp_path):
    result = run(
        command="echo hello-stdout",
        workspace=str(tmp_path),
        timeout=10,
        cpu_limit=1.0,
        memory_limit="256m",
    )
    assert result.exit_code == 0
    assert "hello-stdout" in result.stdout
    assert not result.timed_out


def test_run_captures_nonzero_exit_code(tmp_path):
    result = run(
        command="exit 7",
        workspace=str(tmp_path),
        timeout=10,
        cpu_limit=1.0,
        memory_limit="256m",
    )
    assert result.exit_code == 7


def test_run_enforces_timeout(tmp_path):
    result = run(
        command="sleep 30",
        workspace=str(tmp_path),
        timeout=2,
        cpu_limit=1.0,
        memory_limit="256m",
    )
    assert result.timed_out
    assert result.duration < 20


def test_sandbox_context_manager_runs_multiple_commands(tmp_path):
    tmp_path.chmod(0o777)
    with DockerSandbox("python:3.11-slim", cpu_limit=1.0, memory_limit="256m", workspace=str(tmp_path)) as sandbox:
        first = sandbox.run("echo one > /workspace/marker.txt", timeout=10)
        second = sandbox.run("cat /workspace/marker.txt", timeout=10)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "one" in second.stdout


def test_sandbox_captures_stderr_separately(tmp_path):
    result = run(
        command="echo out-line; echo err-line 1>&2",
        workspace=str(tmp_path),
        timeout=10,
        cpu_limit=1.0,
        memory_limit="256m",
    )
    assert "out-line" in result.stdout
    assert "err-line" in result.stderr
