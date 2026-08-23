"""
BrowserLLM Client for interacting with Claude, ChatGPT, Gemini, and other providers via BrowserLLM.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "agentic" / "prompts"
BROWSERLLM_BIN = shutil.which("browserllm") or str(Path.home() / ".local" / "bin" / "browserllm")


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
    return json.loads(extract_json_block(response_text))


def parse_claude_json(response_text: str) -> Any:
    """Backward-compatible alias for parse_json_response."""
    return parse_json_response(response_text)


def load_prompt(prompt_name: str, **kwargs: Any) -> str:
    """Loads prompt template from agentic/prompts/, auto-injecting deslopify_prompt."""
    prompt_path = PROMPTS_DIR / (prompt_name if prompt_name.endswith(".md") else f"{prompt_name}.md")
    content = prompt_path.read_text(encoding="utf-8")

    if "{deslopify_prompt}" in content:
        deslopify_path = PROMPTS_DIR / "deslopify_text.md"
        deslopify_text = deslopify_path.read_text(encoding="utf-8").strip() if deslopify_path.exists() else ""
        content = content.replace("{deslopify_prompt}", deslopify_text)

    for k, v in kwargs.items():
        content = content.replace(f"{{{k}}}", str(v))

    return content


def ask_browserllm(
    user_prompt: Union[str, Path, List[Dict[str, str]]],
    system_prompt: Optional[str] = None,
    provider: str = "claude",
    schema: Optional[Union[Type[T], Dict[str, Any]]] = None,
    image: Optional[Union[str, Path]] = None,
    timeout: Optional[float] = None,
) -> Union[T, Dict[str, Any], List[Any], str]:
    """Queries a provider (claude, chatgpt, gemini) via BrowserLLM."""
    if isinstance(user_prompt, Path):
        prompt_text = user_prompt.read_text(encoding="utf-8")
    elif isinstance(user_prompt, list):
        prompt_text = "\n\n".join(f"[{m.get('role', 'user').capitalize()}]:\n{m.get('content', '')}" for m in user_prompt)
    elif isinstance(user_prompt, str) and len(user_prompt) < 1024 and "\n" not in user_prompt and Path(user_prompt).is_file():
        prompt_text = Path(user_prompt).read_text(encoding="utf-8")
    else:
        prompt_text = str(user_prompt)

    if system_prompt:
        prompt_text = f"System Instructions:\n{system_prompt.strip()}\n\n---\n\n{prompt_text.strip()}"

    is_pydantic = isinstance(schema, type) and issubclass(schema, BaseModel)
    if schema is not None:
        raw_schema = schema.model_json_schema() if is_pydantic else schema
        if "json" not in prompt_text.lower():
            prompt_text += f"\n\nRespond ONLY with valid JSON conforming to this schema:\n```json\n{json.dumps(raw_schema, indent=2)}\n```"

    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as pf, \
         tempfile.NamedTemporaryFile("r+", suffix=".txt", delete=False, encoding="utf-8") as of:
        prompt_path, output_path = pf.name, of.name
        pf.write(prompt_text)
        pf.flush()

    try:
        cmd = [BROWSERLLM_BIN, "-p", prompt_path, "-P", provider.lower(), "-o", output_path]
        if image:
            cmd.extend(["-i", str(image)])

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        combined_logs = f"{res.stdout}\n{res.stderr}".strip()
        if "FATAL ERROR" in combined_logs or "hard rate limit reached" in combined_logs.lower():
            raise RuntimeError(f"FATAL ERROR: ChatGPT hard rate limit reached in BrowserLLM: {combined_logs}")

        output = Path(output_path).read_text(encoding="utf-8").strip() if Path(output_path).exists() else ""
        if "FATAL ERROR" in output or "hard rate limit reached" in output.lower():
            raise RuntimeError(f"FATAL ERROR: ChatGPT hard rate limit reached in BrowserLLM: {output}")

        if not output:
            if res.returncode != 0:
                raise RuntimeError(f"BrowserLLM ({provider}) failed (code {res.returncode}): {res.stderr.strip() or res.stdout.strip()}")
            if "[*]" in res.stdout or "Launching" in res.stdout:
                raise RuntimeError(f"BrowserLLM ({provider}) did not produce output file. CLI logs:\n{res.stdout.strip()}")
            output = res.stdout.strip()

        if not output:
            raise RuntimeError(f"BrowserLLM ({provider}) returned empty response. Stderr: {res.stderr.strip()}")

        if schema is not None:
            parsed = parse_json_response(output)
            return schema.model_validate(parsed) if is_pydantic else parsed

        return output
    finally:
        for p in (prompt_path, output_path):
            if os.path.exists(p):
                os.remove(p)


# Provider Shortcuts
def ask_claude(user_prompt: Any, **kwargs: Any) -> Any:
    return ask_browserllm(user_prompt, provider="claude", **kwargs)

def ask_chatgpt(user_prompt: Any, **kwargs: Any) -> Any:
    return ask_browserllm(user_prompt, provider="chatgpt", **kwargs)

def ask_gemini(user_prompt: Any, **kwargs: Any) -> Any:
    return ask_browserllm(user_prompt, provider="gemini", **kwargs)

def query_browserllm(prompt_text: str, provider: str = "claude", **kwargs: Any) -> str:
    return str(ask_browserllm(prompt_text, provider=provider, schema=None, **kwargs))

def query_claude(prompt_text: str, **kwargs: Any) -> str:
    return query_browserllm(prompt_text, provider="claude", **kwargs)

def query_chatgpt(prompt_text: str, **kwargs: Any) -> str:
    return query_browserllm(prompt_text, provider="chatgpt", **kwargs)

def query_gemini(prompt_text: str, **kwargs: Any) -> str:
    return query_browserllm(prompt_text, provider="gemini", **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query browser LLMs (Claude, ChatGPT, Gemini).")
    parser.add_argument("-p", "--prompt", type=str, help="Prompt text or file path.")
    parser.add_argument("-t", "--template", type=str, help="Prompt template name in agentic/prompts/.")
    parser.add_argument("-P", "--provider", type=str, default="claude", choices=["claude", "chatgpt", "gemini"])
    parser.add_argument("-i", "--image", type=str, help="Image file path or 'clipboard'.")
    parser.add_argument("-o", "--output", type=str, help="Output file path.")

    args = parser.parse_args()
    if not args.prompt and not args.template:
        parser.error("Either --prompt (-p) or --template (-t) must be provided.")

    prompt_text = load_prompt(args.template) if args.template else args.prompt
    response = ask_browserllm(
        user_prompt=prompt_text,
        provider=args.provider,
        image=args.image,
    )

    if args.output:
        Path(args.output).write_text(str(response), encoding="utf-8")
    else:
        print(response)


if __name__ == "__main__":
    main()
