"""
Generic Groq Structured Output Client.

Provides structured completion capabilities using Groq's JSON Schema / Structured Outputs API
with support for Pydantic models and raw schemas, strict mode validation, and automated
schema normalization.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from groq import Groq
from pydantic import BaseModel

from agentic.schema_utils import build_messages, json_schema_response_format, parse_structured_response

# Ensure environment variables are loaded

T = TypeVar("T", bound=BaseModel)


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

    create_kwargs: Dict[str, Any] = {
        "model": model,
        "messages": build_messages(system_prompt, user_prompt),
        "response_format": json_schema_response_format(schema, schema_name, strict),
        "temperature": temperature,
    }
    if max_tokens is not None:
        create_kwargs["max_tokens"] = max_tokens

    response = client.chat.completions.create(**create_kwargs)
    return parse_structured_response(response.choices[0].message.content or "{}", schema)


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
