import os
import shlex
from pathlib import Path

import pytest

from tools.environments.local import LocalEnvironment
from tools.environments import local as local_mod


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def test_find_bash_prefers_supported_user_shell(monkeypatch):
    monkeypatch.setattr(local_mod, "_IS_WINDOWS", False)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    real_isfile = os.path.isfile

    def _fake_isfile(path: str) -> bool:
        if path in {"/bin/zsh", "/usr/bin/bash", "/bin/bash"}:
            return True
        return real_isfile(path)

    monkeypatch.setattr(local_mod.os.path, "isfile", _fake_isfile)
    monkeypatch.setattr(
        local_mod.shutil,
        "which",
        lambda name: "/usr/bin/bash" if name == "bash" else None,
    )

    assert local_mod._find_bash() == "/bin/zsh"


def test_find_bash_falls_back_to_bash_for_unsupported_user_shell(monkeypatch):
    monkeypatch.setattr(local_mod, "_IS_WINDOWS", False)
    monkeypatch.setenv("SHELL", "/usr/bin/fish")

    real_isfile = os.path.isfile

    def _fake_isfile(path: str) -> bool:
        if path in {"/usr/bin/fish", "/usr/bin/bash", "/bin/bash"}:
            return True
        return real_isfile(path)

    monkeypatch.setattr(local_mod.os.path, "isfile", _fake_isfile)
    monkeypatch.setattr(
        local_mod.shutil,
        "which",
        lambda name: "/usr/bin/bash" if name == "bash" else None,
    )

    assert local_mod._find_bash() == "/usr/bin/bash"


@pytest.mark.skipif(
    not Path("/bin/zsh").is_file(),
    reason="requires /bin/zsh to exercise zsh login-shell PATH behavior",
)
def test_local_environment_uses_user_shell_login_path_for_crontab_resolution(
    monkeypatch, tmp_path
):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    user_bin = tmp_path / "user-bin"
    user_bin.mkdir()
    system_bin = tmp_path / "system-bin"
    system_bin.mkdir()

    _write_executable(
        user_bin / "crontab",
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = \"-l\" ]; then\n"
        "  printf '0 20 * * * /Users/marco/backup_test.sh >> /Users/marco/backup.log 2>&1\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
    )
    _write_executable(
        system_bin / "crontab",
        "#!/bin/sh\n"
        "echo 'no crontab for marco'\n"
        "exit 1\n",
    )

    (home_dir / ".zprofile").write_text(
        f"export PATH={shlex.quote(str(user_bin))}:$PATH\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("ZDOTDIR", str(home_dir))
    monkeypatch.setenv("PATH", f"{system_bin}:/usr/bin:/bin")

    env = LocalEnvironment(cwd=str(tmp_path), timeout=15)
    try:
        result = env.execute("crontab -l", timeout=15)
    finally:
        env.cleanup()

    output = result.get("output", "")
    assert result.get("returncode") == 0
    assert "backup_test.sh" in output
    assert "no crontab for marco" not in output
