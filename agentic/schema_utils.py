"""
Shared prompt, JSON and JSON Schema handling for the LLM clients
(ask_groq, ask_openrouter, ask_browserllm).

Each client only does its own transport; building messages, turning a Pydantic
model or dict into a response schema, pulling JSON out of a reply and validating
it against the schema all live here.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Schema = Union[Type[BaseModel], Dict[str, Any]]


def build_messages(system_prompt: Optional[str], user_prompt: Union[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
    """Chat messages for a system prompt plus a user prompt string or message list."""
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if isinstance(user_prompt, str):
        messages.append({"role": "user", "content": user_prompt})
    elif isinstance(user_prompt, list):
        messages.extend(user_prompt)
    else:
        raise TypeError(f"Expected user_prompt to be str or list of dicts, got {type(user_prompt)}")
    return messages


def is_pydantic_schema(schema: Any) -> bool:
    return isinstance(schema, type) and issubclass(schema, BaseModel)


def schema_to_json(schema: Schema) -> Dict[str, Any]:
    """The JSON Schema dict for a Pydantic model class or a raw schema dict."""
    if is_pydantic_schema(schema):
        return schema.model_json_schema()
    if isinstance(schema, dict):
        return schema
    raise TypeError(f"Expected schema to be a Pydantic BaseModel subclass or a dict, got {type(schema)}")


def json_schema_response_format(schema: Schema, schema_name: str = "structured_response", strict: bool = True) -> Dict[str, Any]:
    """An OpenAI-style ``response_format`` for structured output (used by Groq and OpenRouter)."""
    raw_schema = schema_to_json(schema)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": raw_schema.get("title", schema_name),
            "strict": strict,
            "schema": normalize_schema_for_strict_mode(raw_schema) if strict else raw_schema,
        },
    }


def extract_json_block(text: str) -> str:
    """Extracts JSON substring from text or markdown code fences."""
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_match:
        return fence_match.group(1).strip()

    start = min((pos for pos in (text.find("{"), text.find("[")) if pos != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start != -1 and end != -1 and end >= start:
        return text[start : end + 1]
    return text


def parse_json_response(response_text: str) -> Any:
    """Extracts and parses JSON object or array from LLM response."""
    json_text = extract_json_block(response_text)
    json_text = json_text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return json.loads(json_text)


def parse_structured_response(response_text: str, schema: Schema) -> Any:
    """Parses JSON from an LLM reply and, for a Pydantic schema, validates it into the model."""
    parsed = parse_json_response(response_text)
    return schema.model_validate(parsed) if is_pydantic_schema(schema) else parsed


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
