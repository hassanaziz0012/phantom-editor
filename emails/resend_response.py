"""Reading fields off Resend responses, which are dicts or objects depending on the SDK version."""

from typing import Any


def resend_field(response: Any, name: str, default: Any = None) -> Any:
    """``response[name]`` for a dict response, ``response.name`` for an object one."""
    if isinstance(response, dict):
        return response.get(name, default)
    return getattr(response, name, default)
