from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
_SUPPORTED_STABLE_PLATFORMS = (
    "windows-x64",
    "macos-universal",
    "linux-x64",
)


@dataclass(frozen=True)
class ManifestCompatibility:
    python_min: str
    claude_required_flags: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseAsset:
    platform: str
    name: str
    asset_id: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    channel: str
    repo: str
    tag: str
    release_id: int
    published_at: str
    compatibility: ManifestCompatibility
    assets: tuple[ReleaseAsset, ...]

    def asset_for(self, platform: str) -> ReleaseAsset:
        for asset in self.assets:
            if asset.platform == platform:
                return asset
        raise ValueError(f"manifest does not contain an asset for platform {platform}")


def _require_mapping(payload: dict[str, Any], key: str, *, context: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{context}.{key} must be an object")
    return value


def _require_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def _require_int(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
    minimum: int = 0,
) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{context}.{key} must be an integer >= {minimum}")
    return value


def _require_string_list(payload: dict[str, Any], key: str, *, context: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{context}.{key} must be a list")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{context}.{key}[{index}] must be a non-empty string")
        items.append(item.strip())
    return tuple(items)


def validate_release_identity(
    manifest: ReleaseManifest,
    *,
    repo: str,
    tag: str,
    release_id: int,
    expected_channel: str | None = None,
) -> None:
    if manifest.repo != repo:
        raise ValueError(f"manifest repo {manifest.repo!r} does not match resolved repo {repo!r}")
    if manifest.tag != tag:
        raise ValueError(f"manifest tag {manifest.tag!r} does not match resolved tag {tag!r}")
    if manifest.release_id != release_id:
        raise ValueError(
            f"manifest release_id {manifest.release_id} does not match resolved release_id {release_id}"
        )
    if expected_channel is not None and manifest.channel != expected_channel:
        raise ValueError(
            f"manifest channel {manifest.channel!r} does not match expected channel {expected_channel!r}"
        )


def parse_release_manifest(payload: dict[str, Any]) -> ReleaseManifest:
    if not isinstance(payload, dict):
        raise ValueError("manifest payload must be an object")

    version = _require_string(payload, "version", context="manifest")
    if _VERSION_PATTERN.match(version) is None:
        raise ValueError("manifest.version must be a semantic version")

    channel = _require_string(payload, "channel", context="manifest")
    if channel != "stable":
        raise ValueError("manifest.channel must be 'stable'")
    repo = _require_string(payload, "repo", context="manifest")
    tag = _require_string(payload, "tag", context="manifest")
    expected_tag = f"v{version}"
    if tag != expected_tag:
        raise ValueError(f"manifest.tag must match manifest.version ({expected_tag})")

    release_id = _require_int(payload, "release_id", context="manifest", minimum=1)
    published_at = _require_string(payload, "published_at", context="manifest")

    compatibility_payload = _require_mapping(payload, "compatibility", context="manifest")
    compatibility = ManifestCompatibility(
        python_min=_require_string(compatibility_payload, "python_min", context="manifest.compatibility"),
        claude_required_flags=_require_string_list(
            compatibility_payload,
            "claude_required_flags",
            context="manifest.compatibility",
        ),
    )

    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ValueError("manifest.assets must be a non-empty list")

    assets: list[ReleaseAsset] = []
    seen_platforms: set[str] = set()
    for index, item in enumerate(raw_assets):
        if not isinstance(item, dict):
            raise ValueError(f"manifest.assets[{index}] must be an object")
        asset = ReleaseAsset(
            platform=_require_string(item, "platform", context=f"manifest.assets[{index}]"),
            name=_require_string(item, "name", context=f"manifest.assets[{index}]"),
            asset_id=_require_int(item, "asset_id", context=f"manifest.assets[{index}]", minimum=1),
            size_bytes=_require_int(
                item,
                "size_bytes",
                context=f"manifest.assets[{index}]",
                minimum=1,
            ),
            sha256=_require_string(item, "sha256", context=f"manifest.assets[{index}]"),
        )
        if asset.platform not in _SUPPORTED_STABLE_PLATFORMS:
            raise ValueError(f"manifest.assets[{index}] uses unsupported platform {asset.platform!r}")
        if asset.platform in seen_platforms:
            raise ValueError(f"manifest.assets contains duplicate platform {asset.platform!r}")
        seen_platforms.add(asset.platform)
        assets.append(asset)

    missing_platforms = [platform for platform in _SUPPORTED_STABLE_PLATFORMS if platform not in seen_platforms]
    if missing_platforms:
        raise ValueError(
            "manifest.assets must include stable assets for: " + ", ".join(missing_platforms)
        )
    if len(assets) != len(_SUPPORTED_STABLE_PLATFORMS):
        raise ValueError("manifest.assets must contain exactly the supported stable platform assets")

    return ReleaseManifest(
        version=version,
        channel=channel,
        repo=repo,
        tag=tag,
        release_id=release_id,
        published_at=published_at,
        compatibility=compatibility,
        assets=tuple(assets),
    )
