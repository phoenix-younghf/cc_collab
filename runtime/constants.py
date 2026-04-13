from __future__ import annotations

TASK_TYPES = (
    "research",
    "review",
    "design-review",
    "plan-review",
    "implementation",
)

EXECUTION_MODES = (
    "single-worker",
    "multi-agent",
)

WRITE_POLICIES = (
    "read-only",
    "write-in-place",
    "write-isolated",
)

RESULT_STATUSES = (
    "completed",
    "blocked",
    "failed",
)

TERMINAL_STATES = (
    "archived",
    "integrated",
    "commit-ready",
    "patch-ready",
    "discarded",
    "inspection-required",
)

REQUIRED_CLAUDE_FLAGS = (
    "--print",
    "--output-format",
    "--json-schema",
    "--add-dir",
    "--append-system-prompt",
    "--agents",
)

DEFAULT_CLAUDE_MODEL = "claude-opus-4-6"
CCOLLAB_PROJECT_VERSION = "0.4.4"
CCOLLAB_RELEASE_REPOSITORY = "phoenix-younghf/cc_collab"
INSTALL_METADATA_FILENAME = "install-metadata.json"

CLOSEOUT_MAPPING = {
    "read-only": {"success": {"archived"}, "failure": {"inspection-required"}},
    "write-in-place": {
        "success": {"integrated"},
        "failure": {"patch-ready", "inspection-required"},
    },
    "write-isolated": {
        "success": {"commit-ready", "patch-ready"},
        "failure": {"discarded", "inspection-required"},
    },
}

DEFAULT_PROMPT_BY_TASK = {
    "research": "research",
    "review": "review",
    "design-review": "design-review",
    "plan-review": "plan-review",
    "implementation": "implementation",
}

RESULT_JSON = "result.json"
RESULT_MD = "result.md"
REQUEST_JSON = "request.json"
REQUEST_MD = "request.md"
RUN_LOG = "run.log"
PATCH_FILE = "changes.patch"
