"""Log redaction (PRD §7.4: "logs redact Authorization and fields matching
api_key|token|secret|password").
"""
import re
from typing import Any

_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)
_MASK = "***"


def redact(obj: Any) -> Any:
    """Recursively mask dict keys/values that look like secrets.

    - dict: keys matching `_SENSITIVE_KEY_RE` have their value replaced with
      "***"; other values are recursed into.
    - list/tuple: each item is recursed into.
    - anything else: returned unchanged.
    """
    if isinstance(obj, dict):
        redacted: dict[Any, Any] = {}
        for key, value in obj.items():
            if isinstance(key, str) and _SENSITIVE_KEY_RE.search(key):
                redacted[key] = _MASK
            else:
                redacted[key] = redact(value)
        return redacted
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(redact(item) for item in obj)
    return obj
