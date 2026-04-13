from __future__ import annotations

import json
import subprocess
from unittest import TestCase
from unittest.mock import patch

from runtime.release_manifest import parse_release_manifest
from runtime.updater import (
    DownloadError,
    GhAuthenticationError,
    GhPrerequisiteError,
    ReleaseIdentityError,
    RepoAccessError,
    ResolvedGitHubRelease,
    download_platform_asset,
    download_release_asset,
    download_release_manifest,
    resolve_latest_stable_release,
)


def _stable_release_payload(major: int, minor: int, patch_level: int, *, release_id: int) -> dict[str, object]:
    return {
        "tagName": f"v{major}.{minor}.{patch_level}",
        "databaseId": release_id,
        "publishedAt": "2026-04-13T12:00:00Z",
        "isDraft": False,
        "isPrerelease": False,
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
    def test_resolve_latest_stable_release_uses_canonical_repo_without_limit_ceiling(self) -> None:
        release_list = [
            _stable_release_payload(0, 4, patch_level, release_id=1000 + patch_level)
            for patch_level in range(150)
        ]

        def fake_run(args: list[str], *, text: bool, capture_output: bool, check: bool) -> subprocess.CompletedProcess[str]:
            self.assertEqual(args[:4], ["gh", "release", "list", "--repo"])
            self.assertEqual(args[4], "phoenix-younghf/cc_collab")
            self.assertNotIn("--limit", args)
            self.assertTrue(text)
            self.assertTrue(capture_output)
            self.assertFalse(check)
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(release_list),
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
