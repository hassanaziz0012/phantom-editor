"""Terminal helpers: JSON highlighting and editing text in the user's $EDITOR."""

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def highlight_json(json_str: str) -> str:
    """Highlights JSON string with ANSI color codes, using pygments if available."""
    if not sys.stdout.isatty():
        return json_str

    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import JsonLexer
        return highlight(json_str, JsonLexer(), TerminalFormatter()).rstrip()
    except ImportError:
        pass

    pattern = r'("(?:\\.|[^"\\])*")(\s*:)?|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'

    def colorize(match: re.Match[str]) -> str:
        string_val, colon, bool_val, num_val = match.groups()
        if string_val is not None:
            if colon:
                return f"\033[1;36m{string_val}\033[0m{colon}"
            return f"\033[32m{string_val}\033[0m"
        if bool_val is not None:
            return f"\033[1;35m{bool_val}\033[0m"
        if num_val is not None:
            return f"\033[33m{num_val}\033[0m"
        return match.group(0)

    return re.sub(pattern, colorize, json_str)


def get_editor() -> str:
    """The user's editor ($VISUAL / $EDITOR), else the first common one installed."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return editor
    for candidate in ["nano", "vim", "vi", "gnome-text-editor", "code"]:
        if shutil.which(candidate):
            return candidate
    return "vi"


def edit_text(initial_text: str = "", prefix: str = "edit_") -> str:
    """
    Opens the user's editor on a temp file holding ``initial_text`` and returns what
    was saved. Raises RuntimeError if the editor exits with an error.
    """
    editor = get_editor()
    cmd = shlex.split(editor)
    # GUI editors return immediately unless told to wait for the window to close
    binary_name = Path(cmd[0]).name
    if binary_name == "gnome-text-editor" and "--standalone" not in cmd:
        cmd.append("--standalone")
    elif binary_name == "code" and "-w" not in cmd and "--wait" not in cmd:
        cmd.append("--wait")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix=prefix, delete=False, encoding="utf-8") as tf:
        tf.write(initial_text)
        temp_path = tf.name

    try:
        print(f"Opening {editor}... save and close when done.")
        result = subprocess.run(cmd + [temp_path])
        if result.returncode != 0:
            raise RuntimeError(f"{editor} exited with code {result.returncode}")
        return Path(temp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
