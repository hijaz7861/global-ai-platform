from types import SimpleNamespace

import pytest

from app.ai.sandbox_runner import DockerSandboxRunner


PINNED_IMAGE = "registry.example/sandbox@sha256:" + "a" * 64


def test_runner_requires_digest_pinned_image():
    with pytest.raises(ValueError, match="sha256 digest"):
        DockerSandboxRunner(image="python:3.13-slim")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"memory_limit": "8g"},
        {"cpu_limit": "8.0"},
        {"pids_limit": 1000},
        {"max_output_bytes": 10},
    ],
)
def test_runner_rejects_unbounded_resource_settings(kwargs):
    settings = {"image": PINNED_IMAGE}
    settings.update(kwargs)
    with pytest.raises(ValueError):
        DockerSandboxRunner(**settings)


def test_runner_fails_closed_when_docker_is_not_rootless(monkeypatch):
    runner = DockerSandboxRunner(image=PINNED_IMAGE)
    monkeypatch.setattr(
        "app.ai.sandbox_runner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='["name=seccomp"]'),
    )
    with pytest.raises(RuntimeError, match="Rootless Docker"):
        runner._require_rootless_daemon()


def test_runner_accepts_rootless_daemon_report(monkeypatch):
    runner = DockerSandboxRunner(image=PINNED_IMAGE)
    monkeypatch.setattr(
        "app.ai.sandbox_runner.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='["name=rootless"]'),
    )
    runner._require_rootless_daemon()


def test_runner_rejects_missing_artifact_directory_before_docker(monkeypatch, tmp_path):
    runner = DockerSandboxRunner(image=PINNED_IMAGE)
    called = []
    monkeypatch.setattr(
        runner, "_require_rootless_daemon", lambda: called.append(True)
    )
    with pytest.raises(FileNotFoundError):
        runner.run(
            artifact_directory=tmp_path / "missing",
            trusted_test_directory=tmp_path / "trusted",
            command=["python", "-m", "pytest"],
        )
    assert called == []


def test_runner_rejects_shell_string_command(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    trusted = tmp_path / "trusted"
    candidate.mkdir()
    trusted.mkdir()
    runner = DockerSandboxRunner(image=PINNED_IMAGE)
    monkeypatch.setattr(runner, "_require_rootless_daemon", lambda: None)
    with pytest.raises(ValueError, match="Command must contain"):
        runner.run(
            artifact_directory=candidate,
            trusted_test_directory=trusted,
            command=[],
        )


def test_runner_rejects_nested_candidate_and_trusted_tests(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate"
    trusted = candidate / "tests"
    trusted.mkdir(parents=True)
    runner = DockerSandboxRunner(image=PINNED_IMAGE)
    monkeypatch.setattr(runner, "_require_rootless_daemon", lambda: None)
    with pytest.raises(ValueError, match="separate"):
        runner.run(
            artifact_directory=candidate,
            trusted_test_directory=trusted,
            command=["pytest", "/trusted-tests"],
        )
