import subprocess

import fc_telemetry

from fc_telemetry.version import detect_version


def test_version_is_semver_string():
    parts = fc_telemetry.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_env_wins(monkeypatch):
    monkeypatch.setenv("FC_SERVICE_VERSION", "1.2.3")
    assert detect_version() == "1.2.3"


def test_git_sha_when_in_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("FC_SERVICE_VERSION", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"], check=True)
    v = detect_version(cwd=str(tmp_path))
    assert len(v) >= 7 and v != "unknown"


def test_default_when_no_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("FC_SERVICE_VERSION", raising=False)
    assert detect_version(default="dev", cwd=str(tmp_path)) == "dev"
