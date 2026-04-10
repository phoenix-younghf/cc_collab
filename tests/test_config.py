from __future__ import annotations

import platform
from pathlib import Path, PureWindowsPath
from unittest import TestCase
from unittest.mock import patch

from runtime.config import resolve_claude_model, resolve_paths


class ConfigTests(TestCase):
    def test_resolve_paths_prefers_codex_home_and_xdg(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HOME": "/tmp/home",
                "CODEX_HOME": "/tmp/codex-home",
                "XDG_CONFIG_HOME": "/tmp/xdg-config",
            },
            clear=True,
        ), patch("runtime.config.platform.system", return_value="Linux"):
            paths = resolve_paths()
        self.assertEqual(
            paths.skill_dir,
            Path("/tmp/codex-home/skills/delegate-to-claude-code"),
        )
        self.assertEqual(
            paths.install_root,
            Path("/tmp/home/.local/share/cc_collab/install"),
        )
        self.assertEqual(paths.bin_path, Path("/tmp/home/.local/bin/ccollab"))
        self.assertEqual(paths.config_dir, Path("/tmp/xdg-config/cc_collab"))

    def test_resolve_claude_model_prefers_request_then_env_then_default(self) -> None:
        with patch.dict("os.environ", {"CCOLLAB_CLAUDE_MODEL": "sonnet"}, clear=True):
            self.assertEqual(
                resolve_claude_model({"claude_role": {"model": "opus"}}),
                "opus",
            )
            self.assertEqual(resolve_claude_model({"claude_role": {}}), "sonnet")
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                resolve_claude_model({"claude_role": {}}),
                "claude-opus-4-6",
            )

    def test_resolve_paths_uses_windows_conventions(self) -> None:
        paths = resolve_paths(
            env={
                "USERPROFILE": r"C:\Users\steven",
                "APPDATA": r"C:\Users\steven\AppData\Roaming",
            },
            os_name="nt",
            path_factory=PureWindowsPath,
        )
        self.assertEqual(
            paths.install_root,
            PureWindowsPath(r"C:\Users\steven\AppData\Local\cc_collab\install"),
        )
        self.assertEqual(
            paths.skill_dir,
            PureWindowsPath(
                r"C:\Users\steven\.codex\skills\delegate-to-claude-code"
            ),
        )
        self.assertEqual(
            paths.bin_path,
            PureWindowsPath(r"C:\Users\steven\.local\bin\ccollab.cmd"),
        )
        self.assertEqual(
            paths.config_dir,
            PureWindowsPath(r"C:\Users\steven\AppData\Roaming\cc_collab"),
        )
        self.assertEqual(
            paths.task_root,
            PureWindowsPath(r"C:\Users\steven\workspace\cc_collab\tasks"),
        )

    def test_resolve_paths_uses_macos_install_root(self) -> None:
        with patch("runtime.config.platform.system", return_value="Darwin"):
            paths = resolve_paths(
                env={"HOME": "/Users/steven"},
                os_name="posix",
            )
        expected = Path("/Users/steven/Library/Application Support/cc_collab/install")
        self.assertEqual(paths.install_root, expected)

    def test_resolve_paths_uses_linux_install_root_when_not_macos(self) -> None:
        with patch("runtime.config.platform.system", return_value="Linux"):
            paths = resolve_paths(
                env={"HOME": "/home/steven"},
                os_name="posix",
            )
        expected = Path("/home/steven/.local/share/cc_collab/install")
        self.assertEqual(paths.install_root, expected)
