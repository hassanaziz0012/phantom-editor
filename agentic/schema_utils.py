"""
Shared JSON Schema utilities for LLM structured-output clients.

Providers with strict/constrained decoding modes (Groq, OpenRouter, etc.) impose
the same requirements on JSON Schemas, so the normalization logic lives here and
is imported by each client instead of being duplicated.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


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
