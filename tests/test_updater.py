from __future__ import annotations

import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from runtime.capabilities import ClaudeCapability, PythonCapability
from runtime.release_manifest import parse_release_manifest
from runtime.updater import (
    ChecksumMismatchError,
    CompatibilityError,
    DownloadError,
    GhAuthenticationError,
    GhPrerequisiteError,
    InvalidArchiveError,
    InvalidPayloadError,
    ReleaseIdentityError,
    RepoAccessError,
    ResolvedGitHubRelease,
    UpdateLockedError,
    acquire_update_lock,
    begin_windows_handoff,
    create_update_work_area,
    download_platform_asset,
    download_release_asset,
    download_release_manifest,
    extract_release_archive,
    lock_handoff_active,
    read_update_lock_record,
    recover_or_acquire_lock,
    resolve_latest_stable_release,
    run_compatibility_preflight,
    stage_release_asset,
    stage_release_manifest,
    validate_staged_payload,
    verify_downloaded_archive,
)


def _stable_release_payload(major: int, minor: int, patch_level: int, *, release_id: int) -> dict[str, object]:
    return {
        "tagName": f"v{major}.{minor}.{patch_level}",
        "databaseId": release_id,
        "publishedAt": "2026-04-13T12:00:00Z",
        "isDraft": False,
        "isPrerelease": False,
    }


def _rest_release_payload(major: int, minor: int, patch_level: int, *, release_id: int) -> dict[str, object]:
    return {
        "tag_name": f"v{major}.{minor}.{patch_level}",
        "id": release_id,
        "published_at": "2026-04-13T12:00:00Z",
        "draft": False,
        "prerelease": False,
    }


class FakeGh:
    def __init__(
        self,
        *,
        release_list: list[dict[str, object]] | None = None,
        download_result: bytes = b"",
        error: BaseException | None = None,
        stderr: str = "",
        returncode: int = 0,
    ) -> None:
        self.release_list = release_list or []
        self.download_result = download_result
        self.error = error
        self.stderr = stderr
        self.returncode = returncode
        self.json_calls: list[str] = []
        self.download_calls: list[tuple[str, int, str, int | None]] = []

    def run_json(self, repo: str) -> list[dict[str, object]]:
        self.json_calls.append(repo)
        if self.error is not None:
            raise self.error
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode,
                ["gh", "release", "list", "--repo", repo],
                stderr=self.stderr,
            )
        return self.release_list

    def run_download(
        self,
        repo: str,
        release_id: int,
        asset_name: str,
        asset_id: int | None = None,
    ) -> bytes:
        self.download_calls.append((repo, release_id, asset_name, asset_id))
        if self.error is not None:
            raise self.error
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode,
                ["gh", "api", f"repos/{repo}/releases/{release_id}/assets"],
                stderr=self.stderr,
            )
        return self.download_result


class UpdaterReleaseResolutionTests(TestCase):
    def test_resolve_latest_stable_release_paginates_all_releases_via_gh_api(self) -> None:
        release_pages = [
            [_rest_release_payload(0, 4, patch_level, release_id=1000 + patch_level) for patch_level in range(100)],
            [_rest_release_payload(0, 4, patch_level, release_id=1000 + patch_level) for patch_level in range(100, 150)],
        ]

        def fake_run(args: list[str], *, text: bool, capture_output: bool, check: bool) -> subprocess.CompletedProcess[str]:
            self.assertEqual(args[:4], ["gh", "api", "--paginate", "--slurp"])
            self.assertEqual(args[4], "repos/phoenix-younghf/cc_collab/releases?per_page=100")
            self.assertNotIn("--limit", args)
            self.assertTrue(text)
            self.assertTrue(capture_output)
            self.assertFalse(check)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(release_pages),
                stderr="",
            )

        with patch("runtime.updater.subprocess.run", side_effect=fake_run):
            release = resolve_latest_stable_release()

        self.assertEqual(release.tag, "v0.4.149")
        self.assertEqual(release.release_id, 1149)

    def test_resolve_latest_stable_release_reports_missing_gh(self) -> None:
        runner = FakeGh(error=FileNotFoundError("gh missing"))
        with self.assertRaisesRegex(GhPrerequisiteError, "Install GitHub CLI"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_reports_unauthenticated_gh(self) -> None:
        runner = FakeGh(stderr="gh auth login required", returncode=1)
        with self.assertRaisesRegex(GhAuthenticationError, "gh auth login"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_reports_repo_access_denied(self) -> None:
        runner = FakeGh(stderr="HTTP 404", returncode=1)
        with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
            resolve_latest_stable_release("owner/cc_collab", runner.run_json)

    def test_resolve_latest_stable_release_ignores_drafts_and_prereleases(self) -> None:
        gh = FakeGh(
            release_list=[
                {
                    "tagName": "v0.4.2",
                    "databaseId": 242,
                    "publishedAt": "2026-04-13T12:00:00Z",
                    "isDraft": True,
                    "isPrerelease": False,
                },
                {
                    "tagName": "v0.4.1",
                    "databaseId": 241,
                    "publishedAt": "2026-04-12T12:00:00Z",
                    "isDraft": False,
                    "isPrerelease": False,
                },
            ]
        )
        release = resolve_latest_stable_release("owner/cc_collab", gh.run_json)
        self.assertEqual(release.tag, "v0.4.1")

    def test_resolve_latest_stable_release_prefers_highest_semver(self) -> None:
        gh = FakeGh(
            release_list=[
                {
                    "tagName": "v0.4.2",
                    "databaseId": 242,
                    "publishedAt": "2026-04-13T12:00:00Z",
                    "isDraft": False,
                    "isPrerelease": False,
                },
                {
                    "tagName": "v0.10.0",
                    "databaseId": 300,
                    "publishedAt": "2026-04-12T12:00:00Z",
                    "isDraft": False,
                    "isPrerelease": False,
                },
            ]
        )
        release = resolve_latest_stable_release("owner/cc_collab", gh.run_json)
        self.assertEqual(release.tag, "v0.10.0")

    def test_download_release_manifest_reports_fetch_failure(self) -> None:
        runner = FakeGh(stderr="manifest download failed", returncode=1)
        with self.assertRaisesRegex(DownloadError, "manifest download failed"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_release_manifest_reports_missing_gh(self) -> None:
        runner = FakeGh(error=FileNotFoundError("gh missing"))
        with self.assertRaisesRegex(GhPrerequisiteError, "Install GitHub CLI"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_release_manifest_reports_unauthenticated_gh(self) -> None:
        runner = FakeGh(stderr="gh auth login required", returncode=1)
        with self.assertRaisesRegex(GhAuthenticationError, "gh auth login"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_release_manifest_reports_repo_access_denied(self) -> None:
        runner = FakeGh(stderr="repository not found", returncode=1)
        with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_release_manifest_default_runner_maps_asset_list_http_404_to_repo_access_error(self) -> None:
        def fake_run(args: list[str], *, capture_output: bool, check: bool) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(args[:2], ["gh", "api"])
            self.assertEqual(args[2], "repos/owner/cc_collab/releases/123/assets")
            self.assertTrue(capture_output)
            self.assertFalse(check)
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout=b"",
                stderr=b"HTTP 404",
            )

        with patch("runtime.updater.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
                download_release_manifest(
                    repo="owner/cc_collab",
                    release_id=123,
                    asset_name="ccollab-manifest.json",
                )

    def test_download_release_asset_default_runner_maps_asset_validation_http_403_to_repo_access_error(self) -> None:
        def fake_run(args: list[str], *, capture_output: bool, check: bool) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(args[:2], ["gh", "api"])
            self.assertEqual(args[2], "repos/owner/cc_collab/releases/123/assets")
            self.assertTrue(capture_output)
            self.assertFalse(check)
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout=b"",
                stderr=b"HTTP 403",
            )

        with patch("runtime.updater.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
                download_release_asset(
                    repo="owner/cc_collab",
                    release_id=123,
                    asset_id=111,
                    asset_name="ccollab-windows-x64.zip",
                )

    def test_download_release_asset_default_runner_keeps_final_fetch_http_404_as_download_error(self) -> None:
        def fake_run(args: list[str], *, capture_output: bool, check: bool) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(args[:2], ["gh", "api"])
            self.assertTrue(capture_output)
            self.assertFalse(check)
            if args[2] == "repos/owner/cc_collab/releases/123/assets":
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=b'[{"name":"ccollab-windows-x64.zip","id":111}]',
                    stderr=b"",
                )
            self.assertEqual(args[2:], ["-H", "Accept: application/octet-stream", "repos/owner/cc_collab/releases/assets/111"])
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout=b"",
                stderr=b"HTTP 404",
            )

        with patch("runtime.updater.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(DownloadError, "HTTP 404"):
                download_release_asset(
                    repo="owner/cc_collab",
                    release_id=123,
                    asset_id=111,
                    asset_name="ccollab-windows-x64.zip",
                )

    def test_download_release_manifest_reports_missing_asset_as_download_error(self) -> None:
        runner = FakeGh(stderr="asset not found", returncode=1)
        with self.assertRaisesRegex(DownloadError, "asset not found"):
            download_release_manifest(
                repo="owner/cc_collab",
                release_id=123,
                asset_name="ccollab-manifest.json",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_fetch_failure(self) -> None:
        runner = FakeGh(stderr="asset download failed", returncode=1)
        with self.assertRaisesRegex(DownloadError, "asset download failed"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_missing_gh(self) -> None:
        runner = FakeGh(error=FileNotFoundError("gh missing"))
        with self.assertRaisesRegex(GhPrerequisiteError, "Install GitHub CLI"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_unauthenticated_gh(self) -> None:
        runner = FakeGh(stderr="gh auth login required", returncode=1)
        with self.assertRaisesRegex(GhAuthenticationError, "gh auth login"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_repo_access_denied(self) -> None:
        runner = FakeGh(stderr="repository not found", returncode=1)
        with self.assertRaisesRegex(RepoAccessError, "could not access owner/cc_collab releases"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )

    def test_download_platform_asset_reports_missing_asset_as_download_error(self) -> None:
        runner = FakeGh(stderr="release asset was deleted", returncode=1)
        with self.assertRaisesRegex(DownloadError, "release asset was deleted"):
            download_release_asset(
                repo="owner/cc_collab",
                release_id=123,
                asset_id=111,
                asset_name="ccollab-windows-x64.zip",
                runner=runner.run_download,
            )

    def test_download_platform_asset_rejects_manifest_release_identity_mismatch(self) -> None:
        release = ResolvedGitHubRelease(
            repo="owner/cc_collab",
            tag="v0.4.3",
            release_id=123,
            published_at="2026-04-13T12:00:00Z",
        )
        manifest = parse_release_manifest(
            {
                "version": "0.4.2",
                "channel": "stable",
                "repo": "owner/cc_collab",
                "tag": "v0.4.2",
                "release_id": 123,
                "published_at": "2026-04-13T12:00:00Z",
                "compatibility": {"python_min": "3.9", "claude_required_flags": ["--print"]},
                "assets": [
                    {
                        "platform": "windows-x64",
                        "name": "ccollab-windows-x64.zip",
                        "asset_id": 111,
                        "size_bytes": 42,
                        "sha256": "abc123",
                    },
                    {
                        "platform": "macos-universal",
                        "name": "ccollab-macos-universal.tar.gz",
                        "asset_id": 112,
                        "size_bytes": 84,
                        "sha256": "def456",
                    },
                    {
                        "platform": "linux-x64",
                        "name": "ccollab-linux-x64.tar.gz",
                        "asset_id": 113,
                        "size_bytes": 126,
                        "sha256": "ghi789",
                    }
                ],
            }
        )
        runner = FakeGh(download_result=b"archive")
        with self.assertRaisesRegex(ReleaseIdentityError, "Manifest tag"):
            download_platform_asset(release, manifest, "windows-x64", runner=runner.run_download)
        self.assertEqual(runner.download_calls, [])

    def test_download_platform_asset_uses_manifest_asset_identity(self) -> None:
        release = ResolvedGitHubRelease(
            repo="owner/cc_collab",
            tag="v0.4.2",
            release_id=123,
            published_at="2026-04-13T12:00:00Z",
        )
        manifest = parse_release_manifest(
            {
                "version": "0.4.2",
                "channel": "stable",
                "repo": "owner/cc_collab",
                "tag": "v0.4.2",
                "release_id": 123,
                "published_at": "2026-04-13T12:00:00Z",
                "compatibility": {"python_min": "3.9", "claude_required_flags": ["--print"]},
                "assets": [
                    {
                        "platform": "windows-x64",
                        "name": "ccollab-windows-x64.zip",
                        "asset_id": 111,
                        "size_bytes": 42,
                        "sha256": "abc123",
                    },
                    {
                        "platform": "macos-universal",
                        "name": "ccollab-macos-universal.tar.gz",
                        "asset_id": 112,
                        "size_bytes": 84,
                        "sha256": "def456",
                    },
                    {
                        "platform": "linux-x64",
                        "name": "ccollab-linux-x64.tar.gz",
                        "asset_id": 113,
                        "size_bytes": 126,
                        "sha256": "ghi789",
                    }
                ],
            }
        )
        runner = FakeGh(download_result=b"archive")
        archive = download_platform_asset(release, manifest, "windows-x64", runner=runner.run_download)
        self.assertEqual(archive, b"archive")
        self.assertEqual(
            runner.download_calls,
            [("owner/cc_collab", 123, "ccollab-windows-x64.zip", 111)],
        )


def _seed_install_root(root: Path, *, version: str = "0.4.1") -> Path:
    install_root = root / "install"
    (install_root / "runtime").mkdir(parents=True, exist_ok=True)
    (install_root / "bin").mkdir(parents=True, exist_ok=True)
    (install_root / "runtime" / "version.txt").write_text(version, encoding="utf-8")
    return install_root


def _snapshot_tree(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        snapshot[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return snapshot


def _make_manifest(
    *,
    version: str = "0.4.2",
    python_min: str = "3.9",
    required_flags: tuple[str, ...] = ("--print",),
) -> object:
    return parse_release_manifest(
        {
            "version": version,
            "channel": "stable",
            "repo": "owner/cc_collab",
            "tag": f"v{version}",
            "release_id": 123,
            "published_at": "2026-04-13T12:00:00Z",
            "compatibility": {
                "python_min": python_min,
                "claude_required_flags": list(required_flags),
            },
            "assets": [
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
                    "size_bytes": 84,
                    "sha256": "b" * 64,
                },
                {
                    "platform": "linux-x64",
                    "name": "ccollab-linux-x64.tar.gz",
                    "asset_id": 113,
                    "size_bytes": 126,
                    "sha256": "c" * 64,
                },
            ],
        }
    )


def _seed_staged_payload(root: Path) -> Path:
    staged_root = root / "staged-install"
    staged_root.mkdir(parents=True, exist_ok=True)
    for directory in ("bin", "runtime", "skill", "install", "examples"):
        (staged_root / directory).mkdir()
    (staged_root / "README.md").write_text("# ccollab\n", encoding="utf-8")
    (staged_root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    return staged_root


class UpdaterTransactionTests(TestCase):
    def test_create_update_work_area_rejects_cross_volume_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            with patch("runtime.updater.same_filesystem", return_value=False):
                with self.assertRaises(RuntimeError):
                    create_update_work_area(install_root)

    def test_acquire_update_lock_blocks_second_owner(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock = acquire_update_lock(install_root)
            with self.assertRaises(UpdateLockedError):
                acquire_update_lock(install_root)
            lock.release()

    def test_checksum_mismatch_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root)
            archive_path = root / "payload.zip"
            archive_path.write_bytes(b"payload")
            before = _snapshot_tree(install_root)
            with self.assertRaises(ChecksumMismatchError):
                verify_downloaded_archive(
                    archive_path=archive_path,
                    expected_sha256="0" * 64,
                    expected_size=len(b"payload"),
                )
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_manifest_fetch_failure_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            before = _snapshot_tree(install_root)
            with self.assertRaises(DownloadError):
                stage_release_manifest(
                    install_root=install_root,
                    downloader=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        DownloadError("manifest fetch failed")
                    ),
                )
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_asset_download_failure_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            before = _snapshot_tree(install_root)
            with self.assertRaises(DownloadError):
                stage_release_asset(
                    install_root=install_root,
                    downloader=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        DownloadError("asset download failed")
                    ),
                )
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_invalid_archive_leaves_install_root_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root)
            broken_archive = root / "broken.zip"
            broken_archive.write_bytes(b"not-a-zip")
            stage_root = root / "staged"
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidArchiveError):
                extract_release_archive(broken_archive, stage_root)
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_zip_traversal_archive_is_rejected_without_outside_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root)
            traversal_archive = root / "traversal.zip"
            with zipfile.ZipFile(traversal_archive, mode="w") as archive:
                archive.writestr("../escaped.txt", "escape-attempt")
            stage_root = root / "staged"
            outside_path = root / "escaped.txt"
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidArchiveError):
                extract_release_archive(traversal_archive, stage_root)
            self.assertFalse(outside_path.exists())
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_tar_traversal_archive_is_rejected_without_outside_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root)
            traversal_archive = root / "traversal.tar.gz"
            payload = b"escape-attempt"
            with tarfile.open(traversal_archive, mode="w:gz") as archive:
                member = tarfile.TarInfo(name="../escaped-tar.txt")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            stage_root = root / "staged"
            outside_path = root / "escaped-tar.txt"
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidArchiveError):
                extract_release_archive(traversal_archive, stage_root)
            self.assertFalse(outside_path.exists())
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_tar_symlink_escape_is_rejected_without_outside_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root)
            traversal_archive = root / "traversal-symlink.tar.gz"
            with tarfile.open(traversal_archive, mode="w:gz") as archive:
                member = tarfile.TarInfo(name="runtime/escape-link")
                member.type = tarfile.SYMTYPE
                member.linkname = "../escaped-link-target"
                archive.addfile(member)
            stage_root = root / "staged"
            outside_path = root / "escaped-link-target"
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidArchiveError):
                extract_release_archive(traversal_archive, stage_root)
            self.assertFalse(outside_path.exists())
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_acquire_update_lock_records_owner_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock = acquire_update_lock(install_root, pid=1234, hostname="test-host")
            record = read_update_lock_record(install_root)
            self.assertEqual(record.pid, 1234)
            self.assertEqual(record.hostname, "test-host")
            self.assertEqual(record.install_root, str(install_root.resolve()))
            self.assertIsNotNone(record.acquired_at)
            lock.release()

    def test_recover_or_acquire_lock_refuses_missing_record_without_stale_proof(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock_path = install_root.parent / ".ccollab-update.lock"
            lock_path.write_text("123\n", encoding="utf-8")
            with self.assertRaises(UpdateLockedError):
                recover_or_acquire_lock(install_root, current_pid=999)
            self.assertTrue(lock_path.exists())

    def test_acquire_update_lock_cleans_up_lock_when_metadata_write_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock_path = install_root.parent / ".ccollab-update.lock"
            record_path = install_root.parent / ".ccollab-update.lock.json"
            with patch("runtime.updater._write_lock_record", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    acquire_update_lock(install_root, pid=999)
            self.assertFalse(lock_path.exists())
            self.assertFalse(record_path.exists())

    def test_recover_or_acquire_lock_refuses_unreadable_record_without_stale_proof(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock_path = install_root.parent / ".ccollab-update.lock"
            record_path = install_root.parent / ".ccollab-update.lock.json"
            lock_path.write_text("123\n", encoding="utf-8")
            record_path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(UpdateLockedError):
                recover_or_acquire_lock(install_root, current_pid=999)
            self.assertTrue(lock_path.exists())


class UpdaterCompatibilityTests(TestCase):
    def test_old_python_rejected(self) -> None:
        manifest = _make_manifest(python_min="3.12")
        with patch(
            "runtime.updater.detect_python_capability",
            return_value=PythonCapability(available=True, launcher="python3", remediation=None),
        ):
            with patch("runtime.updater.python_version_tuple", return_value=(3, 11, 9)):
                with self.assertRaises(CompatibilityError):
                    run_compatibility_preflight(manifest)

    def test_missing_required_claude_flag_rejected(self) -> None:
        manifest = _make_manifest(required_flags=("--json-schema",))
        with patch(
            "runtime.updater.detect_claude_capabilities",
            return_value=ClaudeCapability(
                available=True,
                missing_flags=["--json-schema"],
                remediation="upgrade claude",
            ),
        ):
            with self.assertRaises(CompatibilityError):
                run_compatibility_preflight(manifest)


class UpdaterPayloadValidationTests(TestCase):
    def test_missing_runtime_directory_rejects_staged_payload_before_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root, version="0.4.1")
            staged_root = root / "staged-install"
            staged_root.mkdir()
            (staged_root / "bin").mkdir()
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidPayloadError):
                validate_staged_payload(staged_root)
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_missing_skill_directory_rejects_staged_payload_before_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root, version="0.4.1")
            staged_root = _seed_staged_payload(root)
            (staged_root / "skill").rmdir()
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidPayloadError):
                validate_staged_payload(staged_root)
            self.assertEqual(_snapshot_tree(install_root), before)

    def test_missing_readme_rejects_staged_payload_before_swap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            install_root = _seed_install_root(root, version="0.4.1")
            staged_root = _seed_staged_payload(root)
            (staged_root / "README.md").unlink()
            before = _snapshot_tree(install_root)
            with self.assertRaises(InvalidPayloadError):
                validate_staged_payload(staged_root)
            self.assertEqual(_snapshot_tree(install_root), before)


class UpdaterHandoffTests(TestCase):
    def test_begin_windows_handoff_marks_lock_as_transferred(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock = acquire_update_lock(install_root, pid=123, hostname="owner-host")
            record = begin_windows_handoff(install_root, owner_pid=123, helper_pid=456)
            self.assertEqual(record.helper_pid, 456)
            with patch("runtime.updater._pid_is_alive", side_effect=lambda pid: pid == 456):
                self.assertTrue(lock_handoff_active(install_root))
            lock.release()

    def test_stale_lock_recovery_refuses_active_handoff(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = _seed_install_root(Path(tmp))
            lock = acquire_update_lock(install_root, pid=123, hostname="owner-host")
            begin_windows_handoff(install_root, owner_pid=123, helper_pid=456)
            with patch("runtime.updater._pid_is_alive", side_effect=lambda pid: pid == 456):
                with self.assertRaises(UpdateLockedError):
                    recover_or_acquire_lock(install_root, current_pid=999)
            lock.release()
