from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from runtime.constants import CCOLLAB_RELEASE_REPOSITORY
from runtime.release_manifest import ReleaseManifest, validate_release_identity


_SEMVER_TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_AUTH_ERROR_MARKERS = ("gh auth login", "not logged", "authentication failed")
_REPO_ACCESS_MARKERS = ("http 404", "http 403", "not found", "resource not accessible")
_DOWNLOAD_REPO_ACCESS_MARKERS = (
    "http 404",
    "http 403",
    "repository not found",
    "could not resolve to a repository",
    "resource not accessible by integration",
    "access denied to repository",
)

ReleaseListRunner = Callable[[str], list[dict[str, Any]]]
ReleaseDownloadRunner = Callable[[str, int, str, int | None], bytes]


@dataclass(frozen=True)
class ResolvedGitHubRelease:
    repo: str
    tag: str
    release_id: int
    published_at: str


class UpdaterError(RuntimeError):
    """Base class for updater failures."""


class GhPrerequisiteError(UpdaterError):
    """Raised when GitHub CLI is unavailable."""


class GhAuthenticationError(UpdaterError):
    """Raised when GitHub CLI needs authentication."""


class RepoAccessError(UpdaterError):
    """Raised when the repo cannot be read through GitHub CLI."""


class ReleaseLookupError(UpdaterError):
    """Raised when no usable stable release can be resolved."""


class DownloadError(UpdaterError):
    """Raised when a release asset download fails."""


class ReleaseIdentityError(UpdaterError):
    """Raised when manifest and release identity do not match."""


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _command_text(exc: subprocess.CalledProcessError) -> str:
    pieces = [_coerce_text(exc.stderr), _coerce_text(exc.stdout)]
    return "\n".join(piece for piece in pieces if piece).strip()


def _default_release_list_runner(repo: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--json",
            "databaseId,tagName,isDraft,isPrerelease,publishedAt",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    payload = json.loads(result.stdout or "[]")
    if not isinstance(payload, list):
        raise ReleaseLookupError("gh release list returned an unexpected payload")
    items: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            items.append(item)
    return items


def _run_gh_bytes(args: list[str]) -> bytes:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=(result.stderr or b"").decode("utf-8", errors="replace"),
        )
    return result.stdout


def _run_gh_json(args: list[str]) -> Any:
    raw_payload = _run_gh_bytes(args)
    return json.loads(raw_payload.decode("utf-8"))


def _resolve_named_asset_id(repo: str, release_id: int, asset_name: str) -> int:
    payload = _run_gh_json(["api", f"repos/{repo}/releases/{release_id}/assets"])
    if not isinstance(payload, list):
        raise DownloadError(f"release {release_id} assets payload for {repo} was invalid")
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        asset_id = item.get("id")
        if name == asset_name and isinstance(asset_id, int) and not isinstance(asset_id, bool):
            return asset_id
    raise DownloadError(f"release {release_id} does not contain asset {asset_name!r}")


def _validate_bound_asset(repo: str, release_id: int, asset_id: int, asset_name: str) -> None:
    payload = _run_gh_json(["api", f"repos/{repo}/releases/{release_id}/assets"])
    if not isinstance(payload, list):
        raise DownloadError(f"release {release_id} assets payload for {repo} was invalid")
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("name") == asset_name and item.get("id") == asset_id:
            return
    raise DownloadError(
        f"release {release_id} does not contain asset {asset_name!r} with asset_id {asset_id}"
    )


def _default_release_download_runner(
    repo: str,
    release_id: int,
    asset_name: str,
    asset_id: int | None = None,
) -> bytes:
    bound_asset_id = asset_id
    if bound_asset_id is None:
        bound_asset_id = _resolve_named_asset_id(repo, release_id, asset_name)
    else:
        _validate_bound_asset(repo, release_id, bound_asset_id, asset_name)
    return _run_gh_bytes(
        [
            "api",
            "-H",
            "Accept: application/octet-stream",
            f"repos/{repo}/releases/assets/{bound_asset_id}",
        ]
    )


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    match = _SEMVER_TAG_PATTERN.match(tag)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _release_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    tag_name = item["tagName"]
    version = _parse_semver(tag_name)
    if version is None:
        raise ReleaseLookupError(f"release tag {tag_name!r} is not a stable semantic version")
    return version


def _called_process_markers(exc: subprocess.CalledProcessError) -> str:
    return _command_text(exc).lower()


def _translate_release_resolution_error(
    repo: str,
    exc: FileNotFoundError | subprocess.CalledProcessError,
) -> UpdaterError:
    if isinstance(exc, FileNotFoundError):
        return GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'.")
    markers = _called_process_markers(exc)
    if any(marker in markers for marker in _AUTH_ERROR_MARKERS):
        return GhAuthenticationError("Run 'gh auth login' for github.com, then retry.")
    if any(marker in markers for marker in _REPO_ACCESS_MARKERS):
        return RepoAccessError(f"Authenticated GitHub CLI could not access {repo} releases.")
    detail = _command_text(exc)
    if detail:
        return ReleaseLookupError(detail)
    return ReleaseLookupError(f"Unable to resolve releases for {repo}.")


def _translate_release_download_error(
    repo: str,
    exc: FileNotFoundError | subprocess.CalledProcessError,
) -> UpdaterError:
    if isinstance(exc, FileNotFoundError):
        return GhPrerequisiteError("Install GitHub CLI and run 'gh auth login'.")
    markers = _called_process_markers(exc)
    if any(marker in markers for marker in _AUTH_ERROR_MARKERS):
        return GhAuthenticationError("Run 'gh auth login' for github.com, then retry.")
    if any(marker in markers for marker in _DOWNLOAD_REPO_ACCESS_MARKERS):
        return RepoAccessError(f"Authenticated GitHub CLI could not access {repo} releases.")
    detail = _command_text(exc) if isinstance(exc, subprocess.CalledProcessError) else str(exc)
    return DownloadError(detail or f"Failed to download release asset from {repo}")


def _release_id(payload: dict[str, Any]) -> int:
    raw_id = payload.get("databaseId", payload.get("id"))
    if not isinstance(raw_id, int) or isinstance(raw_id, bool) or raw_id < 1:
        raise ReleaseLookupError("gh release payload is missing a usable release id")
    return raw_id


def resolve_latest_stable_release(
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    runner: ReleaseListRunner | None = None,
) -> ResolvedGitHubRelease:
    selected_runner = _default_release_list_runner if runner is None else runner
    try:
        payload = selected_runner(repo)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise _translate_release_resolution_error(repo, exc) from exc

    stable_releases = [
        item
        for item in payload
        if isinstance(item, dict)
        and not bool(item.get("isDraft"))
        and not bool(item.get("isPrerelease"))
        and isinstance(item.get("tagName"), str)
        and _parse_semver(item["tagName"]) is not None
    ]
    if not stable_releases:
        raise ReleaseLookupError(f"No stable releases were found for {repo}.")

    selected = max(stable_releases, key=_release_sort_key)
    return ResolvedGitHubRelease(
        repo=repo,
        tag=selected["tagName"],
        release_id=_release_id(selected),
        published_at=str(selected.get("publishedAt", "")),
    )


def download_release_manifest(
    *,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int,
    asset_name: str,
    runner: ReleaseDownloadRunner | None = None,
) -> dict[str, Any]:
    selected_runner = _default_release_download_runner if runner is None else runner
    try:
        payload = selected_runner(repo, release_id, asset_name, None)
    except (FileNotFoundError, subprocess.CalledProcessError, DownloadError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise _translate_release_download_error(repo, exc) from exc

    try:
        manifest_payload = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Downloaded manifest asset {asset_name} was not valid JSON") from exc
    if not isinstance(manifest_payload, dict):
        raise DownloadError(f"Downloaded manifest asset {asset_name} was not a JSON object")
    return manifest_payload


def download_release_asset(
    *,
    repo: str = CCOLLAB_RELEASE_REPOSITORY,
    release_id: int,
    asset_id: int,
    asset_name: str,
    runner: ReleaseDownloadRunner | None = None,
) -> bytes:
    selected_runner = _default_release_download_runner if runner is None else runner
    try:
        return selected_runner(repo, release_id, asset_name, asset_id)
    except (FileNotFoundError, subprocess.CalledProcessError, DownloadError) as exc:
        if isinstance(exc, DownloadError):
            raise
        raise _translate_release_download_error(repo, exc) from exc


def _validate_release_binding(
    release: ResolvedGitHubRelease,
    manifest: ReleaseManifest,
) -> None:
    try:
        validate_release_identity(
            manifest,
            repo=release.repo,
            tag=release.tag,
            release_id=release.release_id,
            expected_channel="stable",
        )
    except ValueError as exc:
        raise ReleaseIdentityError(str(exc).replace("manifest", "Manifest", 1)) from exc


def download_platform_asset(
    release: ResolvedGitHubRelease,
    manifest: ReleaseManifest,
    platform: str,
    *,
    runner: ReleaseDownloadRunner | None = None,
) -> bytes:
    _validate_release_binding(release, manifest)
    asset = manifest.asset_for(platform)
    return download_release_asset(
        repo=release.repo,
        release_id=release.release_id,
        asset_id=asset.asset_id,
        asset_name=asset.name,
        runner=runner,
    )
