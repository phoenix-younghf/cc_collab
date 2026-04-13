from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from runtime.versioning import (
    InvalidInstallMetadataError,
    InstallMetadata,
    MultipleInstallRootsError,
    build_install_metadata,
    discover_install_root,
    read_install_metadata,
    resolve_platform_identifier,
    write_default_install_metadata,
    write_install_metadata,
)


class VersioningTests(TestCase):
    def test_write_and_read_install_metadata_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            metadata = InstallMetadata(
                version="0.4.2",
                channel="stable",
                repo="owner/cc_collab",
                platform="windows-x64",
                installed_at="2026-04-13T12:34:56Z",
                asset_name="ccollab-windows-x64.zip",
                asset_sha256="abc123",
                install_root=str(root),
            )
            write_install_metadata(root, metadata)
            self.assertEqual(read_install_metadata(root), metadata)

    def test_read_install_metadata_accepts_utf8_bom(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "install-metadata.json").write_bytes(
                (
                    b"\xef\xbb\xbf"
                    + b'{"version":"0.4.2","channel":"stable","repo":"owner/cc_collab",'
                    b'"platform":"linux-x64","installed_at":"2026-04-13T12:34:56Z",'
                    b'"asset_name":"unknown","asset_sha256":"unknown","install_root":"'
                    + str(root).encode("utf-8")
                    + b'"}'
                )
            )
            self.assertEqual(read_install_metadata(root).version, "0.4.2")  # type: ignore[union-attr]

    def test_read_install_metadata_rejects_malformed_json_with_discovery_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            (root / "install-metadata.json").write_text('{"version": ', encoding="utf-8")
            with self.assertRaisesRegex(
                InvalidInstallMetadataError,
                "Reinstall ccollab to repair the install, then retry.",
            ):
                read_install_metadata(root)

    def test_build_install_metadata_uses_shared_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            with patch("runtime.versioning.resolve_platform_identifier", return_value="linux-x64"):
                metadata = build_install_metadata(
                    root,
                    installed_at="2026-04-13T12:34:56Z",
                )
            self.assertEqual(
                metadata,
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="unknown",
                    asset_sha256="unknown",
                    install_root=str(root),
                ),
            )

    def test_write_default_install_metadata_uses_shared_platform_resolution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "install"
            root.mkdir()
            with patch("runtime.versioning.resolve_platform_identifier", return_value="linux-x64"):
                write_default_install_metadata(
                    root,
                    installed_at="2026-04-13T12:34:56Z",
                )
            self.assertEqual(
                read_install_metadata(root),
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="unknown",
                    asset_sha256="unknown",
                    install_root=str(root),
                ),
            )

    def test_discover_install_root_prefers_active_runtime_root(self) -> None:
        with TemporaryDirectory() as tmp:
            active_root = Path(tmp) / "active-install"
            (active_root / "runtime").mkdir(parents=True)
            (active_root / "bin").mkdir()
            write_install_metadata(
                active_root,
                InstallMetadata(
                    version="0.4.2",
                    channel="stable",
                    repo="owner/cc_collab",
                    platform="linux-x64",
                    installed_at="2026-04-13T12:34:56Z",
                    asset_name="unknown",
                    asset_sha256="unknown",
                    install_root=str(active_root),
                ),
            )
            discovery = discover_install_root(
                active_runtime_root=str(active_root),
                env={},
                os_name="posix",
            )
            self.assertEqual(discovery.install_root, active_root)
            self.assertEqual(discovery.status, "installed")
            self.assertEqual(discovery.version, "0.4.2")

    def test_discover_install_root_treats_metadata_less_active_payload_as_legacy_install(self) -> None:
        with TemporaryDirectory() as tmp:
            active_root = Path(tmp) / "active-install"
            (active_root / "runtime").mkdir(parents=True)
            (active_root / "bin").mkdir()
            discovery = discover_install_root(
                active_runtime_root=str(active_root),
                env={},
                os_name="posix",
            )
            self.assertEqual(discovery.install_root, active_root)
            self.assertEqual(discovery.status, "legacy-install")
            self.assertEqual(discovery.version, "unknown")

    def test_discover_install_root_treats_invalid_metadata_as_legacy_install(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            (install_root / "runtime").mkdir(parents=True)
            (install_root / "bin").mkdir()
            (install_root / "install-metadata.json").write_text('{"version": ', encoding="utf-8")
            discovery = discover_install_root(
                active_runtime_root=None,
                env={"HOME": tmp},
                os_name="posix",
                default_install_root=install_root,
            )
            self.assertEqual(discovery.install_root, install_root)
            self.assertEqual(discovery.status, "legacy-install")
            self.assertEqual(discovery.version, "unknown")

    def test_discover_install_root_returns_legacy_when_payload_exists_without_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "install"
            (install_root / "runtime").mkdir(parents=True)
            (install_root / "bin").mkdir()
            discovery = discover_install_root(
                active_runtime_root=None,
                env={"HOME": tmp},
                os_name="posix",
                default_install_root=install_root,
            )
            self.assertEqual(discovery.status, "legacy-install")
            self.assertEqual(discovery.version, "unknown")

    def test_discover_install_root_rejects_conflicting_installs(self) -> None:
        with TemporaryDirectory() as tmp:
            default_root = Path(tmp) / "default-install"
            override_root = Path(tmp) / "override-install"
            (default_root / "runtime").mkdir(parents=True)
            (default_root / "bin").mkdir()
            (override_root / "runtime").mkdir(parents=True)
            (override_root / "bin").mkdir()
            with self.assertRaises(MultipleInstallRootsError):
                discover_install_root(
                    active_runtime_root=None,
                    env={"CCOLLAB_RUNTIME_ROOT": str(override_root), "HOME": tmp},
                    os_name="posix",
                    default_install_root=default_root,
                    reject_conflicting_roots=True,
                )

    def test_resolve_platform_identifier_maps_supported_platforms(self) -> None:
        cases = [
            (("win32", "AMD64"), "windows-x64"),
            (("linux", "x86_64"), "linux-x64"),
            (("darwin", "arm64"), "macos-universal"),
        ]
        for (system_name, machine_name), expected in cases:
            with self.subTest(system_name=system_name, machine_name=machine_name):
                with patch("runtime.versioning.sys.platform", system_name):
                    with patch("runtime.versioning.platform.machine", return_value=machine_name):
                        self.assertEqual(resolve_platform_identifier(), expected)
