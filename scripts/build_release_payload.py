from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.constants import CCOLLAB_RELEASE_REPOSITORY, REQUIRED_CLAUDE_FLAGS
from runtime.release_manifest import parse_release_manifest


REQUIRED_PAYLOAD_DIRS = ("bin", "runtime", "skill", "install", "examples")
REQUIRED_PAYLOAD_FILES = ("README.md", "AGENTS.md")
REQUIRED_PAYLOAD_ENTRIES = REQUIRED_PAYLOAD_DIRS + REQUIRED_PAYLOAD_FILES

ASSETS: tuple[tuple[str, str, str], ...] = (
    ("windows-x64", "ccollab-windows-x64.zip", "zip"),
    ("macos-universal", "ccollab-macos-universal.tar.gz", "tar.gz"),
    ("linux-x64", "ccollab-linux-x64.tar.gz", "tar.gz"),
)

MANIFEST_INPUT_NAME = "ccollab-manifest-input.json"
FINAL_MANIFEST_NAME = "ccollab-manifest.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _require_payload_entries(source_root: Path) -> None:
    for relative in REQUIRED_PAYLOAD_ENTRIES:
        entry = source_root / relative
        if not entry.exists():
            raise ValueError(f"required payload entry {relative!r} is missing from {source_root}")


def _copy_payload_layout(source_root: Path, staging_root: Path) -> Path:
    _require_payload_entries(source_root)

    payload_root = staging_root / "payload"
    payload_root.mkdir(parents=True, exist_ok=True)
    for relative in REQUIRED_PAYLOAD_ENTRIES:
        source = source_root / relative
        destination = payload_root / relative
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return payload_root


def _hash_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _list_archive_entries(path: Path) -> set[str]:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = archive.namelist()
    else:
        with tarfile.open(path, "r:gz") as archive:
            entries = archive.getnames()
    return {entry.removeprefix("./").rstrip("/") for entry in entries if entry}


def _validate_archive(path: Path) -> None:
    entries = _list_archive_entries(path)
    for relative in REQUIRED_PAYLOAD_FILES:
        if relative not in entries:
            raise ValueError(f"{path.name} is missing required file {relative!r}")
    for relative in REQUIRED_PAYLOAD_DIRS:
        has_entry = relative in entries or any(entry.startswith(f"{relative}/") for entry in entries)
        if not has_entry:
            raise ValueError(f"{path.name} is missing required directory {relative!r}")


def _write_zip_archive(payload_root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(payload_root.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(payload_root).as_posix())


def _write_tar_archive(payload_root: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as archive:
        for relative in REQUIRED_PAYLOAD_ENTRIES:
            archive.add(payload_root / relative, arcname=relative)


def build_release_payload(
    *,
    output_dir: Path,
    version: str,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    source_root: Path | None = None,
) -> dict[str, Any]:
    project_root = Path(source_root) if source_root is not None else _repo_root()
    output_dir.mkdir(parents=True, exist_ok=True)

    assets_payload: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ccollab-release-payload-") as tmp:
        payload_root = _copy_payload_layout(project_root, Path(tmp))
        for platform, archive_name, archive_format in ASSETS:
            archive_path = output_dir / archive_name
            if archive_format == "zip":
                _write_zip_archive(payload_root, archive_path)
            else:
                _write_tar_archive(payload_root, archive_path)
            _validate_archive(archive_path)
            assets_payload.append(
                {
                    "platform": platform,
                    "name": archive_name,
                    "size_bytes": archive_path.stat().st_size,
                    "sha256": _hash_sha256(archive_path),
                }
            )

    manifest_input = {
        "version": version,
        "channel": "stable",
        "repo": repo,
        "tag": f"v{version}",
        "assets": assets_payload,
    }
    manifest_input_path = output_dir / MANIFEST_INPUT_NAME
    manifest_input_path.write_text(json.dumps(manifest_input, indent=2) + "\n", encoding="utf-8")
    return manifest_input


def write_release_manifest(
    *,
    output_path: Path,
    version: str,
    repo: str,
    tag: str,
    release_id: int,
    assets: list[dict[str, Any]],
    published_at: str | None = None,
    channel: str = "stable",
) -> dict[str, Any]:
    published_value = published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest_payload = {
        "version": version,
        "channel": channel,
        "repo": repo,
        "tag": tag,
        "release_id": release_id,
        "published_at": published_value,
        "compatibility": {
            "python_min": "3.9",
            "claude_required_flags": list(REQUIRED_CLAUDE_FLAGS),
        },
        "assets": assets,
    }
    parse_release_manifest(manifest_payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
    return manifest_payload


def write_release_manifest_from_input(
    *,
    manifest_input_path: Path,
    output_path: Path,
    release_id: int,
    asset_id_by_name: dict[str, int],
    published_at: str | None = None,
) -> dict[str, Any]:
    manifest_input = json.loads(manifest_input_path.read_text(encoding="utf-8"))
    raw_assets = manifest_input.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("manifest input assets must be a list")

    assets: list[dict[str, Any]] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise ValueError("manifest input assets entries must be objects")
        name = raw_asset["name"]
        asset_id = asset_id_by_name.get(name)
        if asset_id is None:
            raise ValueError(f"missing asset id for {name!r}")
        assets.append(
            {
                "platform": raw_asset["platform"],
                "name": name,
                "asset_id": int(asset_id),
                "size_bytes": int(raw_asset["size_bytes"]),
                "sha256": raw_asset["sha256"],
            }
        )

    return write_release_manifest(
        output_path=output_path,
        version=str(manifest_input["version"]),
        repo=str(manifest_input["repo"]),
        tag=str(manifest_input["tag"]),
        release_id=release_id,
        published_at=published_at,
        assets=assets,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build ccollab release payload archives and manifest artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build platform archives and manifest input artifact")
    build_parser.add_argument("--output-dir", type=Path, required=True)
    build_parser.add_argument("--version", required=True)
    build_parser.add_argument("--repo", default=CCOLLAB_RELEASE_REPOSITORY)
    build_parser.add_argument("--source-root", type=Path)

    manifest_parser = subparsers.add_parser("write-manifest", help="Write ccollab-manifest.json")
    manifest_parser.add_argument("--manifest-input", type=Path, required=True)
    manifest_parser.add_argument("--output-path", type=Path, required=True)
    manifest_parser.add_argument("--release-id", type=int, required=True)
    manifest_parser.add_argument("--windows-asset-id", type=int, required=True)
    manifest_parser.add_argument("--macos-asset-id", type=int, required=True)
    manifest_parser.add_argument("--linux-asset-id", type=int, required=True)
    manifest_parser.add_argument("--published-at")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        build_release_payload(
            output_dir=args.output_dir,
            version=args.version,
            repo=args.repo,
            source_root=args.source_root,
        )
        print(f"Wrote archives and {MANIFEST_INPUT_NAME} to {args.output_dir}")
        return 0

    asset_id_by_name = {
        "ccollab-windows-x64.zip": args.windows_asset_id,
        "ccollab-macos-universal.tar.gz": args.macos_asset_id,
        "ccollab-linux-x64.tar.gz": args.linux_asset_id,
    }
    write_release_manifest_from_input(
        manifest_input_path=args.manifest_input,
        output_path=args.output_path,
        release_id=args.release_id,
        asset_id_by_name=asset_id_by_name,
        published_at=args.published_at,
    )
    print(f"Wrote {FINAL_MANIFEST_NAME} to {args.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
