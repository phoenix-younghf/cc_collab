from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_workflow.py"


def _load_release_workflow_module():
    spec = importlib.util.spec_from_file_location("release_workflow", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load release workflow helper from {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeReleaseApi:
    def __init__(
        self,
        *,
        release_by_tag: dict[str, object] | None = None,
        release_by_tag_sequence: list[dict[str, object] | None] | None = None,
        created_release: dict[str, object] | None = None,
        create_error: BaseException | None = None,
        updated_release: dict[str, object] | None = None,
        uploaded_assets: list[dict[str, object]] | None = None,
    ) -> None:
        self.release_by_tag = release_by_tag
        self.release_by_tag_sequence = list(release_by_tag_sequence or [])
        self.created_release = created_release
        self.create_error = create_error
        self.updated_release = updated_release
        self.uploaded_assets = list(uploaded_assets or [])
        self.calls: list[tuple[object, ...]] = []
        self.deleted_asset_ids: list[int] = []

    def get_release_by_tag(self, repo: str, tag: str) -> dict[str, object] | None:
        self.calls.append(("get_release_by_tag", repo, tag))
        if self.release_by_tag_sequence:
            value = self.release_by_tag_sequence.pop(0)
            return None if value is None else dict(value)
        if self.release_by_tag is None:
            return None
        return dict(self.release_by_tag)

    def create_release(
        self,
        repo: str,
        *,
        tag: str,
        title: str,
        notes: str,
        draft: bool,
    ) -> dict[str, object]:
        self.calls.append(("create_release", repo, tag, title, notes, draft))
        if self.create_error is not None:
            raise self.create_error
        if self.created_release is None:
            raise AssertionError("create_release should not have been called")
        return dict(self.created_release)

    def update_release(self, repo: str, release_id: int, *, draft: bool) -> dict[str, object]:
        self.calls.append(("update_release", repo, release_id, draft))
        if self.updated_release is None:
            raise AssertionError("update_release should not have been called")
        return dict(self.updated_release)

    def delete_asset(self, repo: str, asset_id: int) -> None:
        self.calls.append(("delete_asset", repo, asset_id))
        self.deleted_asset_ids.append(asset_id)

    def upload_asset(self, release: dict[str, object], asset_path: Path) -> dict[str, object]:
        self.calls.append(("upload_asset", int(release["id"]), asset_path.name))
        if not self.uploaded_assets:
            raise AssertionError("upload_asset should not have been called")
        return dict(self.uploaded_assets.pop(0))


class ReleaseWorkflowHelperTests(TestCase):
    def test_ensure_draft_release_creates_missing_release(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            release_by_tag=None,
            created_release={
                "id": 123,
                "tag_name": "v0.4.5",
                "draft": True,
                "assets": [],
                "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
            },
        )

        release = module.ensure_draft_release(api=api, repo="owner/cc_collab", tag="v0.4.5")

        self.assertEqual(int(release["id"]), 123)
        self.assertTrue(bool(release["draft"]))
        self.assertEqual(
            api.calls,
            [
                ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                (
                    "create_release",
                    "owner/cc_collab",
                    "v0.4.5",
                    "v0.4.5",
                    "Draft release for v0.4.5",
                    True,
                ),
            ],
        )

    def test_ensure_draft_release_patches_existing_release_back_to_draft(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            release_by_tag={
                "id": 123,
                "tag_name": "v0.4.5",
                "draft": False,
                "assets": [],
                "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
            },
            updated_release={
                "id": 123,
                "tag_name": "v0.4.5",
                "draft": True,
                "assets": [],
                "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
            },
        )

        release = module.ensure_draft_release(api=api, repo="owner/cc_collab", tag="v0.4.5")

        self.assertEqual(int(release["id"]), 123)
        self.assertTrue(bool(release["draft"]))
        self.assertEqual(
            api.calls,
            [
                ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                ("update_release", "owner/cc_collab", 123, True),
            ],
        )

    def test_ensure_draft_release_retries_after_create_conflict_until_visible(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            release_by_tag_sequence=[
                None,
                None,
                {
                    "id": 123,
                    "tag_name": "v0.4.5",
                    "draft": True,
                    "assets": [],
                    "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
                },
            ],
            create_error=module.GitHubApiError(
                method="POST",
                url="https://api.github.com/repos/owner/cc_collab/releases",
                status=422,
                body='{"message":"Validation Failed"}',
            ),
        )

        release = module.ensure_draft_release(
            api=api,
            repo="owner/cc_collab",
            tag="v0.4.5",
            visibility_attempts=3,
            sleep_seconds=0.0,
        )

        self.assertEqual(int(release["id"]), 123)
        self.assertTrue(bool(release["draft"]))
        self.assertEqual(
            api.calls,
            [
                ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                (
                    "create_release",
                    "owner/cc_collab",
                    "v0.4.5",
                    "v0.4.5",
                    "Draft release for v0.4.5",
                    True,
                ),
                ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
            ],
        )

    def test_upload_release_assets_clobbers_existing_named_asset(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            uploaded_assets=[
                {"id": 201, "name": "ccollab-windows-x64.zip"},
            ]
        )
        release = {
            "id": 123,
            "tag_name": "v0.4.5",
            "draft": True,
            "assets": [
                {"id": 101, "name": "ccollab-windows-x64.zip"},
            ],
            "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
        }

        with TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "ccollab-windows-x64.zip"
            asset_path.write_bytes(b"zip-bytes")

            uploaded_assets = module.upload_release_assets(
                api=api,
                repo="owner/cc_collab",
                release=release,
                asset_paths=[asset_path],
                clobber=True,
            )

        self.assertEqual(api.deleted_asset_ids, [101])
        self.assertEqual(uploaded_assets, [{"id": 201, "name": "ccollab-windows-x64.zip"}])
        self.assertEqual(
            api.calls,
            [
                ("delete_asset", "owner/cc_collab", 101),
                ("upload_asset", 123, "ccollab-windows-x64.zip"),
            ],
        )

    def test_capture_release_assets_writes_expected_github_outputs(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            release_by_tag={
                "id": 123,
                "tag_name": "v0.4.5",
                "draft": True,
                "assets": [
                    {"id": 111, "name": "ccollab-windows-x64.zip"},
                    {"id": 112, "name": "ccollab-macos-universal.tar.gz"},
                    {"id": 113, "name": "ccollab-linux-x64.tar.gz"},
                ],
                "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
            }
        )

        with TemporaryDirectory() as tmp:
            github_output = Path(tmp) / "github-output.txt"
            outputs = module.capture_release_assets(
                api=api,
                repo="owner/cc_collab",
                tag="v0.4.5",
                github_output_path=github_output,
            )

            self.assertEqual(
                outputs,
                {
                    "release_id": "123",
                    "windows_asset_id": "111",
                    "macos_asset_id": "112",
                    "linux_asset_id": "113",
                },
            )
            self.assertEqual(
                github_output.read_text(encoding="utf-8").splitlines(),
                [
                    "release_id=123",
                    "windows_asset_id=111",
                    "macos_asset_id=112",
                    "linux_asset_id=113",
                ],
            )

    def test_capture_release_assets_retries_until_assets_are_visible(self) -> None:
        module = _load_release_workflow_module()
        api = FakeReleaseApi(
            release_by_tag_sequence=[
                {
                    "id": 123,
                    "tag_name": "v0.4.5",
                    "draft": True,
                    "assets": [
                        {"id": 111, "name": "ccollab-windows-x64.zip"},
                    ],
                    "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
                },
                {
                    "id": 123,
                    "tag_name": "v0.4.5",
                    "draft": True,
                    "assets": [
                        {"id": 111, "name": "ccollab-windows-x64.zip"},
                        {"id": 112, "name": "ccollab-macos-universal.tar.gz"},
                        {"id": 113, "name": "ccollab-linux-x64.tar.gz"},
                    ],
                    "upload_url": "https://uploads.example.test/releases/123/assets{?name,label}",
                },
            ]
        )

        with TemporaryDirectory() as tmp:
            github_output = Path(tmp) / "github-output.txt"
            outputs = module.capture_release_assets(
                api=api,
                repo="owner/cc_collab",
                tag="v0.4.5",
                github_output_path=github_output,
                visibility_attempts=2,
                sleep_seconds=0.0,
            )

            self.assertEqual(
                outputs,
                {
                    "release_id": "123",
                    "windows_asset_id": "111",
                    "macos_asset_id": "112",
                    "linux_asset_id": "113",
                },
            )
            self.assertEqual(
                api.calls,
                [
                    ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                    ("get_release_by_tag", "owner/cc_collab", "v0.4.5"),
                ],
            )

    def test_build_release_asset_outputs_maps_uploaded_assets(self) -> None:
        module = _load_release_workflow_module()

        outputs = module.build_release_asset_outputs(
            release_id=123,
            assets=[
                {"id": 111, "name": "ccollab-windows-x64.zip"},
                {"id": 112, "name": "ccollab-macos-universal.tar.gz"},
                {"id": 113, "name": "ccollab-linux-x64.tar.gz"},
            ],
        )

        self.assertEqual(
            outputs,
            {
                "release_id": "123",
                "windows_asset_id": "111",
                "macos_asset_id": "112",
                "linux_asset_id": "113",
            },
        )
