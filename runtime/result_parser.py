from __future__ import annotations

import json


def _parse_json_fragment(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def parse_result(stdout: str) -> dict:
    payload = _parse_json_fragment(stdout)
    nested = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(nested, str):
        try:
            return _parse_json_fragment(nested)
        except json.JSONDecodeError:
            return payload
    return payload
