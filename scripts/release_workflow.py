from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request


API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
ASSET_OUTPUT_NAMES = {
    "ccollab-windows-x64.zip": "windows_asset_id",
    "ccollab-macos-universal.tar.gz": "macos_asset_id",
    "ccollab-linux-x64.tar.gz": "linux_asset_id",
}


class GitHubApiError(RuntimeError):
    def __init__(self, *, method: str, url: str, status: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        detail = body.strip() or "no response body"
        super().__init__(f"GitHub API {method} {url} failed with HTTP {status}: {detail}")


class GitHubReleaseApi:
    def __init__(self, *, token: str, opener: Any = request.urlopen) -> None:
        self.token = token
        self.opener = opener

    def get_release_by_tag(self, repo: str, tag: str) -> dict[str, Any] | None:
        url = f"{API_ROOT}/repos/{repo}/releases/tags/{parse.quote(tag)}"
        try:
            return self._request_json("GET", url)
        except GitHubApiError as exc:
            if exc.status == 404:
                return None
            raise

    def get_release(self, repo: str, release_id: int) -> dict[str, Any]:
        return self._request_json("GET", f"{API_ROOT}/repos/{repo}/releases/{release_id}")

    def create_release(
        self,
        repo: str,
        *,
        tag: str,
        title: str,
        notes: str,
        draft: bool,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"{API_ROOT}/repos/{repo}/releases",
            payload={
                "tag_name": tag,
                "name": title,
                "body": notes,
                "draft": draft,
            },
        )

    def update_release(self, repo: str, release_id: int, *, draft: bool) -> dict[str, Any]:
        return self._request_json(
            "PATCH",
            f"{API_ROOT}/repos/{repo}/releases/{release_id}",
            payload={"draft": draft},
        )

    def delete_asset(self, repo: str, asset_id: int) -> None:
        self._request_json(
            "DELETE",
            f"{API_ROOT}/repos/{repo}/releases/assets/{asset_id}",
            allow_empty=True,
        )

    def upload_asset(self, release: dict[str, Any], asset_path: Path) -> dict[str, Any]:
        upload_template = _require_string(release, "upload_url", context="release")
        upload_url = upload_template.split("{", 1)[0]
        upload_url = f"{upload_url}?name={parse.quote(asset_path.name)}"
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        return self._request_json(
            "POST",
            upload_url,
            raw_body=asset_path.read_bytes(),
            extra_headers={"Content-Type": content_type},
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "ccollab-release-workflow",
        }
        if extra_headers:
            headers.update(extra_headers)

        if payload is not None and raw_body is not None:
            raise ValueError("payload and raw_body are mutually exclusive")

        body: bytes | None = raw_body
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")

        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener(req) as response:
                raw = response.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise GitHubApiError(method=method, url=url, status=exc.code, body=detail) from exc

        if not raw:
            if allow_empty:
                return {}
            raise RuntimeError(f"GitHub API {method} {url} returned an empty response body")

        try:
            payload_obj = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"GitHub API {method} {url} returned invalid JSON") from exc
        if not isinstance(payload_obj, dict):
            raise RuntimeError(f"GitHub API {method} {url} returned a non-object JSON payload")
        return payload_obj


def ensure_draft_release(
    *,
    api: Any,
    repo: str,
    tag: str,
    visibility_attempts: int = 5,
    sleep_seconds: float = 2.0,
) -> dict[str, Any]:
    release = api.get_release_by_tag(repo, tag)
    if release is None:
        try:
            release = api.create_release(
                repo,
                tag=tag,
                title=tag,
                notes=f"Draft release for {tag}",
                draft=True,
            )
        except GitHubApiError as exc:
            if exc.status != 422:
                raise
            release = _retry_get_release_by_tag(
                api=api,
                repo=repo,
                tag=tag,
                attempts=visibility_attempts,
                sleep_seconds=sleep_seconds,
            )
            if release is None:
                raise

    release_id = _require_int(release, "id", context="release")
    if not bool(release.get("draft")):
        release = api.update_release(repo, release_id, draft=True)

    if not bool(release.get("draft")):
        raise RuntimeError(f"release {tag} exists but is not in draft state")
    return release


def upload_release_assets(
    *,
    api: Any,
    repo: str,
    release: dict[str, Any],
    asset_paths: Iterable[Path],
    clobber: bool,
) -> list[dict[str, Any]]:
    existing_assets = {
        _require_string(asset, "name", context="release asset"): asset
        for asset in _iter_assets(release)
    }
    uploaded_assets: list[dict[str, Any]] = []
    for raw_path in asset_paths:
        asset_path = Path(raw_path)
        if clobber:
            existing = existing_assets.get(asset_path.name)
            if existing is not None:
                api.delete_asset(repo, _require_int(existing, "id", context=f"release asset {asset_path.name}"))
        uploaded_assets.append(api.upload_asset(release, asset_path))
    return uploaded_assets


def capture_release_assets(
    *,
    api: Any,
    repo: str,
    tag: str,
    github_output_path: Path | None = None,
    visibility_attempts: int = 5,
    sleep_seconds: float = 2.0,
) -> dict[str, str]:
    release: dict[str, Any] | None = None
    outputs: dict[str, str] | None = None
    for attempt in range(max(1, visibility_attempts)):
        release = api.get_release_by_tag(repo, tag)
        if release is not None:
            outputs = _build_release_outputs(release)
            if outputs is not None:
                break
        if attempt + 1 < max(1, visibility_attempts):
            time.sleep(sleep_seconds)

    if release is None:
        raise RuntimeError(f"release {tag} does not exist")
    if outputs is None:
        missing = sorted(set(ASSET_OUTPUT_NAMES) - {asset.get("name") for asset in _iter_assets(release)})
        raise RuntimeError(
            f"release {tag} is missing required assets: {', '.join(repr(name) for name in missing)}"
        )

    if github_output_path is not None:
        write_github_outputs(github_output_path, outputs)
    return outputs


def write_github_outputs(path: Path, outputs: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def _iter_assets(release: dict[str, Any]) -> list[dict[str, Any]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise RuntimeError("release assets payload is invalid")
    normalized: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("release assets payload is invalid")
        normalized.append(asset)
    return normalized


def _build_release_outputs(release: dict[str, Any]) -> dict[str, str] | None:
    outputs = {"release_id": str(_require_int(release, "id", context="release"))}
    asset_id_by_name = {
        _require_string(asset, "name", context="release asset"): str(
            _require_int(asset, "id", context="release asset")
        )
        for asset in _iter_assets(release)
    }
    for asset_name, output_name in ASSET_OUTPUT_NAMES.items():
        asset_id = asset_id_by_name.get(asset_name)
        if asset_id is None:
            return None
        outputs[output_name] = asset_id
    return outputs


def _retry_get_release_by_tag(
    *,
    api: Any,
    repo: str,
    tag: str,
    attempts: int,
    sleep_seconds: float,
) -> dict[str, Any] | None:
    for attempt in range(max(1, attempts)):
        release = api.get_release_by_tag(repo, tag)
        if release is not None:
            return release
        if attempt + 1 < max(1, attempts):
            time.sleep(sleep_seconds)
    return None


def _require_int(payload: dict[str, Any], key: str, *, context: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{context} is missing a valid {key}")
    return value


def _require_string(payload: dict[str, Any], key: str, *, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{context} is missing a valid {key}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage ccollab GitHub draft releases via the REST API")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure-draft-release", help="Ensure a draft release exists for a tag")
    ensure_parser.add_argument("--repo", required=True)
    ensure_parser.add_argument("--tag", required=True)
    ensure_parser.add_argument("--github-output", type=Path)

    upload_parser = subparsers.add_parser("upload-assets", help="Upload one or more assets to a release")
    upload_parser.add_argument("--repo", required=True)
    upload_parser.add_argument("--release-id", type=int, required=True)
    upload_parser.add_argument("--clobber", action="store_true")
    upload_parser.add_argument("asset_paths", nargs="+", type=Path)

    capture_parser = subparsers.add_parser(
        "capture-release-assets",
        help="Resolve the release and required asset IDs for manifest generation",
    )
    capture_parser.add_argument("--repo", required=True)
    capture_parser.add_argument("--tag", required=True)
    capture_parser.add_argument("--github-output", type=Path, required=True)

    return parser


def _build_api_from_env() -> GitHubReleaseApi:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN or GH_TOKEN before running the release workflow helper.")
    return GitHubReleaseApi(token=token)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    api = _build_api_from_env()

    try:
        if args.command == "ensure-draft-release":
            release = ensure_draft_release(api=api, repo=args.repo, tag=args.tag)
            if args.github_output is not None:
                write_github_outputs(
                    args.github_output,
                    {"release_id": str(_require_int(release, "id", context="release"))},
                )
            print(f"release {args.tag} is ready as draft")
            return 0

        if args.command == "upload-assets":
            release = api.get_release(args.repo, args.release_id)
            uploaded_assets = upload_release_assets(
                api=api,
                repo=args.repo,
                release=release,
                asset_paths=args.asset_paths,
                clobber=bool(args.clobber),
            )
            print("uploaded: " + ", ".join(_require_string(asset, "name", context="uploaded asset") for asset in uploaded_assets))
            return 0

        capture_release_assets(
            api=api,
            repo=args.repo,
            tag=args.tag,
            github_output_path=args.github_output,
        )
        print(f"captured release asset ids for {args.tag}")
        return 0
    except (GitHubApiError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
