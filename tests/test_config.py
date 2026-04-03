from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from runtime.config import resolve_paths


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
        ):
            paths = resolve_paths()
        self.assertEqual(
            paths.skill_dir,
            Path("/tmp/codex-home/skills/delegate-to-claude-code"),
        )
        self.assertEqual(paths.bin_path, Path("/tmp/home/.local/bin/ccollab"))
        self.assertEqual(paths.config_dir, Path("/tmp/xdg-config/cc_collab"))
