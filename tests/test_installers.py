from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless

from runtime.constants import CCOLLAB_PROJECT_VERSION
from runtime.versioning import read_install_metadata, resolve_platform_identifier


REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or "/bin/bash"


def _platform_install_root(home: Path) -> Path:
    if os.name == "nt":
        return home / "AppData" / "Local" / "cc_collab" / "install"
    if platform.system() == "Darwin":
        return home / "Library" / "Application Support" / "cc_collab" / "install"
    return home / ".local" / "share" / "cc_collab" / "install"


def _user_bin_dir(home: Path) -> Path:
    if os.name == "nt":
        return home / ".local" / "bin"
    return home / ".local" / "bin"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_fake_python(bin_dir: Path, *, supported: bool = True) -> Path:
    log_path = bin_dir / "fake-python.log"
    real_python = sys.executable
    script = f"""#!{BASH}
set -euo pipefail
printf 'ARGS=%s\\n' "$*" >>"${{CCOLLAB_FAKE_PYTHON_LOG}}"
printf 'PYTHONPATH=%s\\n' "${{PYTHONPATH-}}" >>"${{CCOLLAB_FAKE_PYTHON_LOG}}"
if [ "${{1-}}" = "-c" ]; then
    if [ {1 if supported else 0} -eq 0 ]; then
        exit 1
    fi
    exec {real_python!r} "$@"
fi
if [ "${{1-}}" = "-m" ] && [ "${{2-}}" = "runtime.versioning" ]; then
    exec {real_python!r} "$@"
fi
if [ "${{1-}}" = "-m" ] && [ "${{2-}}" = "runtime.cli" ] && [ "${{3-}}" = "doctor" ]; then
    printf '%b' "${{CCOLLAB_FAKE_DOCTOR_STDOUT:-Doctor status: OK\\n}}"
    exit "${{CCOLLAB_FAKE_DOCTOR_EXIT:-0}}"
fi
exit 0
"""
    _write_executable(bin_dir / "python3", script)
    _write_executable(bin_dir / "python", script)
    return log_path


def _make_fake_brew(bin_dir: Path, succeeds: bool) -> Path:
    log_path = bin_dir / "fake-brew.log"
    script = f"""#!{BASH}
set -euo pipefail
printf 'brew %s\\n' "$*" >>"{log_path}"
echo "brew install python"
exit {0 if succeeds else 1}
"""
    _write_executable(bin_dir / "brew", script)
    return log_path


def _make_missing_brew_shim(bin_dir: Path) -> None:
    script = f"""#!{BASH}
set -euo pipefail
echo "missing brew" >&2
exit 127
"""
    _write_executable(bin_dir / "brew", script)


def _make_missing_python_shims(bin_dir: Path) -> None:
    script = f"""#!{BASH}
set -euo pipefail
echo "missing python" >&2
exit 127
"""
    _write_executable(bin_dir / "python3", script)
    _write_executable(bin_dir / "python", script)
    _write_executable(bin_dir / "py", script)


def _make_fake_winget(bin_dir: Path, succeeds: bool) -> Path:
    log_path = bin_dir / "fake-winget.log"
    script = f"""#!{BASH}
set -euo pipefail
printf 'winget %s\\n' "$*" >>"{log_path}"
echo "winget install Python"
exit {0 if succeeds else 1}
"""
    _write_executable(bin_dir / "winget", script)
    return log_path


def _make_fake_windows_python(bin_dir: Path) -> Path:
    log_path = bin_dir / "fake-python.log"
    real_python = sys.executable.replace("\\", "\\\\")
    script = rf"""@echo off
setlocal
>> "%CCOLLAB_FAKE_PYTHON_LOG%" echo ARGS=%*
>> "%CCOLLAB_FAKE_PYTHON_LOG%" echo PYTHONPATH=%PYTHONPATH%
if "%~1"=="-3" (
    shift
)
if "%~1"=="-c" (
    "{real_python}" %*
    exit /b %errorlevel%
)
if "%~1"=="-m" if "%~2"=="runtime.versioning" (
    "{real_python}" %*
    exit /b %errorlevel%
)
if "%~1"=="-m" if "%~2"=="runtime.cli" if "%~3"=="doctor" (
    echo Doctor status: OK
    exit /b 0
)
exit /b 0
"""
    for name in ("py.cmd", "python.cmd", "python3.cmd"):
        _write_executable(bin_dir / name, script)
    return log_path


def _build_archive_style_source_tree(temp_root: Path) -> Path:
    source_root = temp_root / "archive-source"
    source_root.mkdir(parents=True, exist_ok=True)
    for relative in ("bin", "examples", "install", "runtime", "skill"):
        shutil.copytree(REPO_ROOT / relative, source_root / relative)
    for relative in ("README.md", "AGENTS.md"):
        shutil.copy2(REPO_ROOT / relative, source_root / relative)
    return source_root


def _run_install_all_sh(
    *,
    temp_root: Path,
    source_root: Path,
    with_python: bool,
    fake_python_supported: bool = True,
    fake_brew: bool = False,
    brew_succeeds: bool = False,
    fake_doctor_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    home = temp_root / "home"
    bin_dir = temp_root / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if with_python:
        log_path = _make_fake_python(bin_dir, supported=fake_python_supported)
    else:
        log_path = bin_dir / "fake-python.log"
        _make_missing_python_shims(bin_dir)
    if fake_brew:
        _make_fake_brew(bin_dir, brew_succeeds)
    else:
        _make_missing_brew_shim(bin_dir)
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "CCOLLAB_FAKE_PYTHON_LOG": str(log_path),
        "CCOLLAB_FAKE_DOCTOR_EXIT": str(fake_doctor_exit),
        "CCOLLAB_FAKE_DOCTOR_STDOUT": "Doctor status: FAIL\n[fail] claude: claude command available\n",
    }
    return subprocess.run(
        [BASH, str(source_root / "install" / "install-all.sh")],
        cwd=source_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_install_script_with_fake_python(
    *,
    temp_root: str,
    source_root: Path | None = None,
    fake_python_supported: bool = True,
    fake_doctor_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    root = REPO_ROOT if source_root is None else source_root
    return _run_install_all_sh(
        temp_root=Path(temp_root),
        source_root=root,
        with_python=True,
        fake_python_supported=fake_python_supported,
        fake_doctor_exit=fake_doctor_exit,
    )


def run_install_all_sh_without_python(
    *,
    temp_root: str,
    fake_brew: bool,
    brew_succeeds: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run_install_all_sh(
        temp_root=Path(temp_root),
        source_root=REPO_ROOT,
        with_python=False,
        fake_brew=fake_brew,
        brew_succeeds=brew_succeeds,
    )


def run_installed_launcher_with_fake_python(
    arguments: list[str],
    *,
    temp_root: str,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    temp_path = Path(temp_root)
    install_result = run_install_script_with_fake_python(temp_root=temp_root)
    home = temp_path / "home"
    launcher = _user_bin_dir(home) / "ccollab"
    python_log = temp_path / "bin" / "fake-python.log"
    result = subprocess.run(
        [str(launcher), *arguments],
        env={
            "HOME": str(home),
            "CODEX_HOME": str(home / ".codex"),
            "PATH": os.pathsep.join([str(temp_path / "bin"), os.environ.get("PATH", "")]),
            "CCOLLAB_FAKE_PYTHON_LOG": str(python_log),
            "CCOLLAB_FAKE_DOCTOR_EXIT": "0",
            "CCOLLAB_FAKE_DOCTOR_STDOUT": "Doctor status: OK\n",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, install_result, python_log


def _pwsh_command() -> str:
    return shutil.which("pwsh") or "pwsh"


def _cmd_command() -> str:
    return shutil.which("cmd") or "cmd"


def run_install_all_ps1_without_python(
    *,
    temp_root: str,
    fake_winget: bool,
    winget_succeeds: bool = False,
) -> subprocess.CompletedProcess[str]:
    temp_path = Path(temp_root)
    home = temp_path / "home"
    bin_dir = temp_path / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    _make_missing_python_shims(bin_dir)
    if fake_winget:
        _make_fake_winget(bin_dir, winget_succeeds)
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
    }
    return subprocess.run(
        [
            _pwsh_command(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install" / "install-all.ps1"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_install_script_with_fake_windows_python(
    *,
    temp_root: str,
) -> subprocess.CompletedProcess[str]:
    temp_path = Path(temp_root)
    home = temp_path / "home"
    bin_dir = temp_path / "bin"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = _make_fake_windows_python(bin_dir)
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "CODEX_HOME": str(home / ".codex"),
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        "CCOLLAB_FAKE_PYTHON_LOG": str(log_path),
    }
    return subprocess.run(
        [
            _pwsh_command(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPO_ROOT / "install" / "install-all.ps1"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_installed_windows_launcher_with_fake_python(
    arguments: list[str],
    *,
    temp_root: str,
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str], Path]:
    temp_path = Path(temp_root)
    install_result = run_install_script_with_fake_windows_python(temp_root=temp_root)
    home = temp_path / "home"
    launcher = _user_bin_dir(home) / "ccollab.cmd"
    python_log = temp_path / "bin" / "fake-python.log"
    result = subprocess.run(
        [_cmd_command(), "/c", str(launcher), *arguments],
        env={
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "APPDATA": str(home / "AppData" / "Roaming"),
            "CODEX_HOME": str(home / ".codex"),
            "PATH": os.pathsep.join([str(temp_path / "bin"), os.environ.get("PATH", "")]),
            "CCOLLAB_FAKE_PYTHON_LOG": str(python_log),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, install_result, python_log


class InstallerTests(TestCase):
    def test_install_all_sh_copies_runtime_and_runs_doctor(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            home = Path(tmp) / "home"
            install_root = _platform_install_root(home)
            python_log = Path(tmp) / "bin" / "fake-python.log"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((install_root / "runtime" / "cli.py").exists())
            self.assertIn("ARGS=-m runtime.cli doctor", python_log.read_text(encoding="utf-8"))

    def test_install_from_archive_tree_does_not_require_git_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            source_root = _build_archive_style_source_tree(temp_root)
            result = run_install_script_with_fake_python(
                temp_root=tmp,
                source_root=source_root,
            )
            install_root = _platform_install_root(temp_root / "home")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((source_root / ".git").exists())
            self.assertTrue((install_root / "runtime" / "cli.py").exists())

    def test_install_all_sh_writes_install_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            install_root = _platform_install_root(Path(tmp) / "home")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((install_root / "install-metadata.json").exists())
            metadata = read_install_metadata(install_root)
            self.assertEqual(metadata.version, CCOLLAB_PROJECT_VERSION)  # type: ignore[union-attr]
            self.assertEqual(metadata.channel, "stable")  # type: ignore[union-attr]
            self.assertEqual(metadata.repo, "owner/cc_collab")  # type: ignore[union-attr]
            self.assertEqual(metadata.platform, resolve_platform_identifier())  # type: ignore[union-attr]
            self.assertEqual(metadata.asset_name, "unknown")  # type: ignore[union-attr]
            self.assertEqual(metadata.asset_sha256, "unknown")  # type: ignore[union-attr]
            self.assertEqual(metadata.install_root, str(install_root))  # type: ignore[union-attr]

    @skipUnless(shutil.which("pwsh"), "pwsh required for PowerShell bootstrap behavior")
    def test_install_all_ps1_writes_install_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_windows_python(temp_root=tmp)
            install_root = Path(tmp) / "home" / "AppData" / "Local" / "cc_collab" / "install"
            self.assertEqual(result.returncode, 0)
            self.assertTrue((install_root / "install-metadata.json").exists())

    def test_installed_unix_launcher_forwards_arguments_from_install_root(self) -> None:
        with TemporaryDirectory() as tmp:
            result, install_result, python_log = run_installed_launcher_with_fake_python(
                ["doctor"],
                temp_root=tmp,
            )
            home = Path(tmp) / "home"
            install_root = _platform_install_root(home)
            self.assertEqual(install_result.returncode, 0, install_result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            log_text = python_log.read_text(encoding="utf-8")
            self.assertIn("ARGS=-m runtime.cli doctor", log_text)
            self.assertIn(str(install_root), log_text)
            self.assertNotIn(str(REPO_ROOT), log_text)

    @skipUnless(
        shutil.which("cmd") and shutil.which("pwsh"),
        "cmd and pwsh required for Windows launcher forwarding",
    )
    def test_installed_windows_launcher_forwards_arguments_from_install_root(self) -> None:
        with TemporaryDirectory() as tmp:
            result, install_result, python_log = run_installed_windows_launcher_with_fake_python(
                ["doctor"],
                temp_root=tmp,
            )
            home = Path(tmp) / "home"
            install_root = home / "AppData" / "Local" / "cc_collab" / "install"
            self.assertEqual(install_result.returncode, 0, install_result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
            log_text = python_log.read_text(encoding="utf-8")
            self.assertIn("runtime.cli doctor", log_text)
            self.assertIn(str(install_root), log_text)
            self.assertNotIn(str(REPO_ROOT), log_text)

    def test_unix_bootstrap_attempts_brew_then_surfaces_guidance_when_python_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_sh_without_python(
                temp_root=tmp,
                fake_brew=True,
                brew_succeeds=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("brew", result.stdout.lower())
            self.assertIn("install python", result.stderr.lower())

    def test_unix_bootstrap_rejects_unsupported_python_version(self) -> None:
        with TemporaryDirectory() as tmp:
            result = _run_install_all_sh(
                temp_root=Path(tmp),
                source_root=REPO_ROOT,
                with_python=True,
                fake_python_supported=False,
                fake_brew=True,
                brew_succeeds=False,
            )
            python_log = (Path(tmp) / "bin" / "fake-python.log").read_text(encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ARGS=-c", python_log)
            self.assertIn("install python", result.stderr.lower())

    def test_windows_scripts_use_py_default_python3_channel(self) -> None:
        launcher = (REPO_ROOT / "bin" / "ccollab.cmd").read_text(encoding="utf-8")
        installer = (REPO_ROOT / "install" / "install-all.ps1").read_text(encoding="utf-8")
        self.assertIn("call :try_python py -3", launcher)
        self.assertNotIn("py -3.9", launcher)
        self.assertIn('@("py", "-3")', installer)
        self.assertNotIn('-3.9', installer)

    def test_unix_bootstrap_surfaces_manual_guidance_when_python_and_brew_are_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_sh_without_python(
                temp_root=tmp,
                fake_brew=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("python 3", result.stderr.lower())
            self.assertIn("brew", result.stderr.lower())

    def test_installers_refresh_path_for_current_session(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(temp_root=tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(".local/bin", result.stdout)

    def test_install_succeeds_even_when_doctor_reports_missing_claude(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_script_with_fake_python(
                temp_root=tmp,
                fake_doctor_exit=1,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("claude", result.stdout.lower())

    @skipUnless(shutil.which("pwsh"), "pwsh required for PowerShell bootstrap behavior")
    def test_windows_bootstrap_attempts_winget_then_surfaces_guidance_when_python_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_install_all_ps1_without_python(
                temp_root=tmp,
                fake_winget=True,
                winget_succeeds=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("winget", result.stdout.lower())
            self.assertIn("install python", result.stderr.lower())
