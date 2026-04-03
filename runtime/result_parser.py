from __future__ import annotations

import json


def parse_result(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stdout[start : end + 1])
        raise
