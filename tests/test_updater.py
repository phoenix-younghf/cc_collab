from __future__ import annotations

import subprocess
from unittest import TestCase

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
