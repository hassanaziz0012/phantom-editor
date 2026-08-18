"""
Generic Groq Structured Output Client.

Provides structured completion capabilities using Groq's JSON Schema / Structured Outputs API
with support for Pydantic models and raw schemas, strict mode validation, and automated
schema normalization.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel

# Ensure environment variables are loaded
load_dotenv()

T = TypeVar("T", bound=BaseModel)


def normalize_schema_for_strict_mode(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively normalizes a JSON Schema dictionary to meet Groq's strict mode requirements:
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


def ask_groq(
    system_prompt: str,
    user_prompt: Union[str, List[Dict[str, str]]],
    schema: Union[Type[T], Dict[str, Any]],
    model: str = "openai/gpt-oss-120b",
    schema_name: str = "structured_response",
    temperature: float = 0.2,
    strict: bool = True,
    max_tokens: Optional[int] = None,
    client: Optional[Groq] = None,
) -> Union[T, Dict[str, Any]]:
    """
    Queries Groq with structured output schema and returns either a validated Pydantic model
    instance or a parsed JSON dictionary.

    Args:
        system_prompt: The system message defining model behavior and instructions.
        user_prompt: The user prompt string or a list of message dicts.
        schema: Either a Pydantic BaseModel subclass or a JSON schema dictionary.
        model: Groq model identifier (default: "openai/gpt-oss-120b").
        schema_name: Name of the schema in response_format (default: "structured_response").
        temperature: Sampling temperature (default: 0.2 for deterministic extraction).
        strict: Whether to enforce strict mode constrained decoding (default: True).
        max_tokens: Maximum tokens in response.
        client: Optional pre-configured Groq client instance.

    Returns:
        Instance of Pydantic model (if BaseModel subclass was passed as schema) or parsed Dict.
    """
    if client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set.")
        client = Groq(api_key=api_key)

    is_pydantic_model = isinstance(schema, type) and issubclass(schema, BaseModel)

    # Generate JSON schema
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

    # Normalize schema if strict mode is enabled
    target_schema = (
        normalize_schema_for_strict_mode(raw_schema) if strict else raw_schema
    )

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

    # Construct request payload
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_title,
            "strict": strict,
            "schema": target_schema,
        },
    }

    create_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": response_format,
        "temperature": temperature,
    }
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens

    response = client.chat.completions.create(**create_kwargs)

    raw_content = response.choices[0].message.content or "{}"
    parsed_json = json.loads(raw_content)

    if is_pydantic_model:
        return schema.model_validate(parsed_json)
    return parsed_json


if __name__ == "__main__":
    # Self-test when run directly
    print("Testing ask_groq with a sample Pydantic model...")

    class SentimentTest(BaseModel):
        sentiment: str
        confidence: float
        summary: str

    res = ask_groq(
        system_prompt="You are a sentiment analyzer. Return structured JSON.",
        user_prompt="I really enjoyed the latest release! The performance improvements are impressive.",
        schema=SentimentTest,
        model="openai/gpt-oss-120b",
    )
    print("Success! Result:")
    print(res)
