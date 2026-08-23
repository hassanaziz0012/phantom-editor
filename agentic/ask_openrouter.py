"""
Generic OpenRouter Structured Output Client.

Provides structured completion capabilities using OpenRouter's JSON Schema / Structured Outputs API
with support for Pydantic models and raw schemas, strict mode validation, and automated
schema normalization.
"""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar, Union

from dotenv import load_dotenv
from pydantic import BaseModel
import requests

# Ensure environment variables are loaded
load_dotenv()

logger = logging.getLogger("phantom.agentic.openrouter")

T = TypeVar("T", bound=BaseModel)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"

# Module-level pointer to the current working key index in the discovered pool
_CURRENT_KEY_INDEX: int = 0


def get_openrouter_api_keys() -> List[Tuple[str, str]]:
    """
    Discovers all OpenRouter API keys defined in the environment following the
    pattern `OPENROUTER_API_KEY_<N>`, sorted in ascending numerical order.

    Returns:
        List of tuples: (env_var_name, api_key_value) e.g. [("OPENROUTER_API_KEY_1", "sk-...")]
    """
    pattern = re.compile(r"^OPENROUTER_API_KEY_(\d+)$")
    indexed_keys: List[Tuple[int, str, str]] = []

    for env_name, env_val in os.environ.items():
        match = pattern.match(env_name)
        if match:
            idx = int(match.group(1))
            val = env_val.strip()
            if val:
                indexed_keys.append((idx, env_name, val))

    indexed_keys.sort(key=lambda item: item[0])
    return [(name, val) for _, name, val in indexed_keys]


def reset_key_index() -> None:
    """Resets the active key index back to the first available key."""
    global _CURRENT_KEY_INDEX
    _CURRENT_KEY_INDEX = 0


def get_active_key_index() -> int:
    """Returns the current active key index in the discovered pool."""
    return _CURRENT_KEY_INDEX


def normalize_schema_for_strict_mode(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively normalizes a JSON Schema dictionary to meet strict mode requirements:
    1. Sets `additionalProperties: false` on every object type schema.
    2. Ensures all defined properties are included in the `required` array.
    """
    normalized = deepcopy(schema)

    def _walk(node: Any):
        if not isinstance(node, dict):
            return

        # If it's an object type or contains properties
        if node.get("type") == "object" or "properties" in node:
            if "additionalProperties" not in node or node["additionalProperties"] is not False:
                node["additionalProperties"] = False

            if "properties" in node and isinstance(node["properties"], dict):
                # In strict mode, all properties should be in required
                existing_required = set(node.get("required", []))
                for prop_name in node["properties"].keys():
                    existing_required.add(prop_name)
                node["required"] = sorted(list(existing_required))

                # Recursively process properties
                for prop_val in node["properties"].values():
                    _walk(prop_val)

        # Process array items
        if "items" in node:
            _walk(node["items"])

        # Process anyOf, allOf, oneOf
        for combiner in ("anyOf", "allOf", "oneOf"):
            if combiner in node and isinstance(node[combiner], list):
                for sub_schema in node[combiner]:
                    _walk(sub_schema)

        # Process definitions / $defs
        for def_key in ("$defs", "definitions"):
            if def_key in node and isinstance(node[def_key], dict):
                for def_schema in node[def_key].values():
                    _walk(def_schema)

    _walk(normalized)
    return normalized


def ask_openrouter(
    system_prompt: str,
    user_prompt: Union[str, List[Dict[str, str]]],
    schema: Optional[Union[Type[T], Dict[str, Any]]] = None,
    model: str = DEFAULT_OPENROUTER_MODEL,
    schema_name: str = "structured_response",
    temperature: float = 0.2,
    strict: bool = True,
    max_tokens: Optional[int] = None,
    api_key: Optional[str] = None,
    timeout: float = 120.0,
) -> Union[T, Dict[str, Any], str]:
    """
    Queries OpenRouter API with structured output schema (or standard completion)
    and returns either a validated Pydantic model instance, a parsed JSON dictionary,
    or raw response text.

    Sequentially attempts keys from OPENROUTER_API_KEY_1, OPENROUTER_API_KEY_2, etc.
    If a key returns an HTTP 429 rate limit error, it is discarded for future requests
    and execution automatically advances to the next available key. If all keys return
    429, execution terminates.

    Args:
        system_prompt: The system message defining model behavior and instructions.
        user_prompt: The user prompt string or a list of message dicts.
        schema: Optional Pydantic BaseModel subclass or JSON schema dictionary.
        model: OpenRouter model identifier (default: "google/gemma-4-26b-a4b-it:free").
        schema_name: Name of the schema in response_format (default: "structured_response").
        temperature: Sampling temperature (default: 0.2).
        strict: Whether to enforce strict mode constrained decoding (default: True).
        max_tokens: Maximum tokens in response.
        api_key: Optional explicit OpenRouter API key (overrides environment pool).
        timeout: Request timeout in seconds (default: 120.0).

    Returns:
        Instance of Pydantic model (if BaseModel subclass was passed), parsed Dict, or str.
    """
    global _CURRENT_KEY_INDEX

    # Prepare candidate keys
    if api_key:
        key_pool: List[Tuple[str, str]] = [("EXPLICIT_KEY", api_key)]
        start_idx = 0
    else:
        key_pool = get_openrouter_api_keys()
        if not key_pool:
            raise ValueError(
                "No OpenRouter API keys found. Please define OPENROUTER_API_KEY_1, "
                "OPENROUTER_API_KEY_2, etc. in your .env file."
            )
        if _CURRENT_KEY_INDEX >= len(key_pool):
            raise RuntimeError(
                f"All available OpenRouter API keys ({len(key_pool)}) have been exhausted "
                "due to rate limits (HTTP 429). Execution terminated."
            )
        start_idx = _CURRENT_KEY_INDEX

    # Prepare messages
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if isinstance(user_prompt, str):
        messages.append({"role": "user", "content": user_prompt})
    elif isinstance(user_prompt, list):
        messages.extend(user_prompt)
    else:
        raise TypeError(
            f"Expected user_prompt to be str or list of dicts, got {type(user_prompt)}"
        )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    is_pydantic_model = False
    if schema is not None:
        is_pydantic_model = isinstance(schema, type) and issubclass(schema, BaseModel)
        if is_pydantic_model:
            raw_schema = schema.model_json_schema()
            schema_title = raw_schema.get("title", schema_name)
        elif isinstance(schema, dict):
            raw_schema = schema
            schema_title = raw_schema.get("title", schema_name)
        else:
            raise TypeError(
                f"Expected schema to be a Pydantic BaseModel subclass or a dict, got {type(schema)}"
            )

        target_schema = (
            normalize_schema_for_strict_mode(raw_schema) if strict else raw_schema
        )

        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_title,
                "strict": strict,
                "schema": target_schema,
            },
        }

    # Iterate through keys sequentially, advancing on 429
    idx = start_idx
    while idx < len(key_pool):
        key_name, current_key = key_pool[idx]
        headers = {
            "Authorization": f"Bearer {current_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/phantom-editor",
            "X-Title": "Phantom Editor",
        }

        try:
            response = requests.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Network error while communicating with OpenRouter using '{key_name}': {exc}"
            ) from exc

        # Handle rate limiting: mark key as exhausted and advance to next
        if response.status_code == 429:
            logger.warning(
                "OpenRouter API key '%s' hit rate limit (HTTP 429). Advancing to next key...",
                key_name,
            )
            idx += 1
            if not api_key:
                _CURRENT_KEY_INDEX = idx
            continue

        if not response.ok:
            raise RuntimeError(
                f"OpenRouter API error (HTTP {response.status_code}) using '{key_name}': {response.text}"
            )

        data = response.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error response using '{key_name}': {data['error']}")

        choices = data.get("choices")
        if not choices:
            raise RuntimeError(f"OpenRouter returned empty choices using '{key_name}': {data}")

        raw_content = choices[0].get("message", {}).get("content", "")

        if schema is None:
            return raw_content

        # If raw_content is wrapped in markdown code blocks like ```json ... ```, strip them
        cleaned_content = raw_content.strip()
        if cleaned_content.startswith("```json"):
            cleaned_content = cleaned_content[7:]
        elif cleaned_content.startswith("```"):
            cleaned_content = cleaned_content[3:]
        if cleaned_content.endswith("```"):
            cleaned_content = cleaned_content[:-3]
        cleaned_content = cleaned_content.strip()

        parsed_json = json.loads(cleaned_content)

        if is_pydantic_model:
            return schema.model_validate(parsed_json)
        return parsed_json

    # All keys were exhausted with 429
    raise RuntimeError(
        f"All available OpenRouter API keys ({len(key_pool)}) were exhausted "
        "due to rate limits (HTTP 429). Execution terminated."
    )


if __name__ == "__main__":
    print("Testing ask_openrouter with a sample Pydantic model...")

    class SentimentTest(BaseModel):
        sentiment: str
        confidence: float
        summary: str

    try:
        res = ask_openrouter(
            system_prompt="You are a sentiment analyzer. Return structured JSON.",
            user_prompt="I really enjoyed the latest release! The performance improvements are impressive.",
            schema=SentimentTest,
            model=DEFAULT_OPENROUTER_MODEL,
        )
        print("Success! Result:")
        print(res)
    except Exception as e:
        print(f"Test finished with result/error: {e}")
