from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from runtime.release_manifest import parse_release_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_release_payload.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"
ARCHIVE_NAMES = (
    "ccollab-windows-x64.zip",
    "ccollab-macos-universal.tar.gz",
    "ccollab-linux-x64.tar.gz",
)


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("build_release_payload", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release builder from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive_entries(path: Path) -> set[str]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    else:
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
    return {name.removeprefix("./").rstrip("/") for name in names if name}


class ReleasePayloadTests(TestCase):
    def test_build_payload_archives_expected_layout(self) -> None:
        module = _load_builder_module()

        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            module.build_release_payload(output_dir=output_dir, version="0.4.2")

            for archive_name in ARCHIVE_NAMES:
                self.assertTrue((output_dir / archive_name).exists(), archive_name)
            self.assertTrue((output_dir / "ccollab-manifest-input.json").exists())

    def test_build_payload_archives_include_required_runtime_entries(self) -> None:
        module = _load_builder_module()

        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            module.build_release_payload(output_dir=output_dir, version="0.4.2")

            for archive_name in ARCHIVE_NAMES:
                entries = _archive_entries(output_dir / archive_name)
                self.assertIn("README.md", entries)
                self.assertIn("AGENTS.md", entries)
                self.assertIn("runtime/cli.py", entries)
                self.assertIn("install/install-all.sh", entries)
                self.assertTrue(any(name.startswith("skill/") for name in entries))
                self.assertTrue(any(name.startswith("examples/") for name in entries))

            windows_entries = _archive_entries(output_dir / "ccollab-windows-x64.zip")
            linux_entries = _archive_entries(output_dir / "ccollab-linux-x64.tar.gz")
            macos_entries = _archive_entries(output_dir / "ccollab-macos-universal.tar.gz")
            self.assertIn("bin/ccollab.cmd", windows_entries)
            self.assertIn("bin/ccollab", linux_entries)
            self.assertIn("bin/ccollab", macos_entries)

    def test_build_payload_emits_manifest_input_with_size_and_sha(self) -> None:
        module = _load_builder_module()

        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            module.build_release_payload(output_dir=output_dir, version="0.4.2")

            payload = json.loads((output_dir / "ccollab-manifest-input.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], "0.4.2")
            self.assertEqual(payload["tag"], "v0.4.2")
            assets = payload["assets"]
            self.assertEqual({asset["name"] for asset in assets}, set(ARCHIVE_NAMES))
            for asset in assets:
                self.assertIsInstance(asset["size_bytes"], int)
                self.assertGreater(asset["size_bytes"], 0)
                self.assertEqual(len(asset["sha256"]), 64)

    def test_script_cli_build_command_runs_from_repo_root(self) -> None:
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_release_payload.py",
                    "build",
                    "--output-dir",
                    str(output_dir),
                    "--version",
                    "0.4.2",
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output_dir / "ccollab-manifest-input.json").exists())

    def test_write_manifest_binds_release_and_asset_identity(self) -> None:
        module = _load_builder_module()

        with TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "ccollab-manifest.json"
            manifest = module.write_release_manifest(
                output_path=output_path,
                version="0.4.2",
                repo="owner/cc_collab",
                tag="v0.4.2",
                release_id=123,
                published_at="2026-04-13T12:00:00Z",
                assets=[
                    {
                        "platform": "windows-x64",
                        "name": "ccollab-windows-x64.zip",
                        "asset_id": 111,
                        "size_bytes": 42,
                        "sha256": "a" * 64,
                    },
                    {
                        "platform": "macos-universal",
                        "name": "ccollab-macos-universal.tar.gz",
                        "asset_id": 112,
                        "size_bytes": 43,
                        "sha256": "b" * 64,
                    },
                    {
                        "platform": "linux-x64",
                        "name": "ccollab-linux-x64.tar.gz",
                        "asset_id": 113,
                        "size_bytes": 44,
                        "sha256": "c" * 64,
                    },
                ],
            )

            self.assertEqual(manifest["release_id"], 123)
            self.assertEqual({asset["asset_id"] for asset in manifest["assets"]}, {111, 112, 113})
            parsed = parse_release_manifest(json.loads(output_path.read_text(encoding="utf-8")))
            self.assertEqual(parsed.release_id, 123)
            self.assertEqual(parsed.asset_for("windows-x64").asset_id, 111)

    def test_release_workflow_keeps_draft_and_uploads_manifest_last(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertIn("on:", workflow)
        self.assertIn("tags:", workflow)
        self.assertIn("v*.*.*", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("python3 scripts/build_release_payload.py build", workflow)
        self.assertIn("python3 scripts/build_release_payload.py write-manifest", workflow)
        self.assertIn("release_id", workflow)
        self.assertIn("asset_id", workflow)
        self.assertLess(
            workflow.find("ccollab-linux-x64.tar.gz"),
            workflow.find("ccollab-manifest.json"),
        )
