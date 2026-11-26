import sys

from shared import colors
from shared.timecodes import format_srt_timestamp


class Colors:
    """The names the review scripts use, mapped onto shared.colors."""
    HEADER = colors.MAGENTA
    BLUE = colors.BLUE
    CYAN = colors.CYAN
    GREEN = colors.GREEN
    WARNING = colors.YELLOW
    FAIL = colors.RED
    ENDC = colors.RESET
    BOLD = colors.BOLD
    UNDERLINE = colors.UNDERLINE
    DIM = colors.DIM

def colorize(text: str, color_code: str) -> str:
    """Colors text if output is a TTY."""
    if sys.stdout.isatty():
        return f"{color_code}{text}{Colors.ENDC}"
    return text

def parse_time_to_seconds(time_str: str, default: float = 0.0) -> float:
    """Parses HH:MM:SS.mmm or MM:SS.mmm or raw float seconds into float seconds.
    If parsing fails, returns default (or raises ValueError if default is None).
    """
    time_str = time_str.strip().replace(',', '.')
    parts = time_str.split(':')
    try:
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m = int(parts[0])
            s = float(parts[1])
            return m * 60 + s
        else:
            return float(time_str)
    except ValueError:
        if default is not None:
            return default
        raise ValueError(f"Invalid timestamp format: '{time_str}'")

def format_time(seconds: float, separator: str = ".") -> str:
    """Converts seconds to HH:MM:SS.mmm (or HH:MM:SS,mmm if separator is ',') format."""
    return format_srt_timestamp(seconds, separator)
