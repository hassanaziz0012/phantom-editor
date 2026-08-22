"""
Generic OpenRouter Structured Output Client.

Provides structured completion capabilities using OpenRouter's JSON Schema / Structured Outputs API
with support for Pydantic models and raw schemas, strict mode validation, and automated
schema normalization.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from dotenv import load_dotenv
from pydantic import BaseModel
import requests

# Ensure environment variables are loaded
load_dotenv()

T = TypeVar("T", bound=BaseModel)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"


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

    Args:
        system_prompt: The system message defining model behavior and instructions.
        user_prompt: The user prompt string or a list of message dicts.
        schema: Optional Pydantic BaseModel subclass or JSON schema dictionary.
        model: OpenRouter model identifier (default: "google/gemma-4-26b-a4b-it:free").
        schema_name: Name of the schema in response_format (default: "structured_response").
        temperature: Sampling temperature (default: 0.2).
        strict: Whether to enforce strict mode constrained decoding (default: True).
        max_tokens: Maximum tokens in response.
        api_key: Optional OpenRouter API key (defaults to OPENROUTER_API_KEY env var).
        timeout: Request timeout in seconds (default: 120.0).

    Returns:
        Instance of Pydantic model (if BaseModel subclass was passed), parsed Dict, or str.
    """
    resolved_api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not resolved_api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable is not set.")

    headers = {
        "Authorization": f"Bearer {resolved_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/phantom-editor",
        "X-Title": "Phantom Editor",
    }

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

    response = requests.post(
        OPENROUTER_API_URL,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if not response.ok:
        raise RuntimeError(
            f"OpenRouter API error (HTTP {response.status_code}): {response.text}"
        )

    data = response.json()
    if "error" in data:
        raise RuntimeError(f"OpenRouter error response: {data['error']}")

    choices = data.get("choices")
    if not choices:
        raise RuntimeError(f"OpenRouter returned empty choices: {data}")

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
