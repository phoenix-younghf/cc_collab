from __future__ import annotations

from unittest import TestCase

from runtime.release_manifest import parse_release_manifest, validate_release_identity


class ReleaseManifestTests(TestCase):
    def test_parse_manifest_rejects_non_stable_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "stable"):
            parse_release_manifest(
                {
                    "version": "0.4.2",
                    "channel": "beta",
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
                        },
                    ],
                }
            )

    def test_parse_manifest_requires_release_identity_fields(self) -> None:
        payload = {
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
        manifest = parse_release_manifest(payload)
        self.assertEqual(manifest.release_id, 123)
        self.assertEqual(manifest.asset_for("windows-x64").asset_id, 111)

    def test_parse_manifest_rejects_missing_supported_platform_asset(self) -> None:
        with self.assertRaisesRegex(ValueError, "linux-x64"):
            parse_release_manifest(
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
                    ],
                }
            )

    def test_parse_manifest_rejects_unsupported_platform_asset(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported platform"):
            parse_release_manifest(
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
                        },
                        {
                            "platform": "linux-arm64",
                            "name": "ccollab-linux-arm64.tar.gz",
                            "asset_id": 114,
                            "size_bytes": 128,
                            "sha256": "jkl012",
                        },
                    ],
                }
            )

    def test_parse_manifest_rejects_missing_asset_id(self) -> None:
        with self.assertRaises(ValueError):
            parse_release_manifest(
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
                            "size_bytes": 42,
                            "sha256": "abc123",
                        }
                    ],
                }
            )

    def test_validate_release_identity_rejects_mismatched_release_binding(self) -> None:
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
        with self.assertRaisesRegex(ValueError, "release_id"):
            validate_release_identity(
                manifest,
                repo="owner/cc_collab",
                tag="v0.4.2",
                release_id=999,
                expected_channel="stable",
            )
