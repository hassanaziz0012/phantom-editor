import sys

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    DIM = '\033[2m'

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
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int(round((seconds % 1) * 1000))
    if milliseconds == 1000:
        milliseconds = 0
        secs += 1
        if secs == 60:
            secs = 0
            minutes += 1
            if minutes == 60:
                minutes = 0
                hours += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"

def format_seconds_to_hhmmss(seconds: float) -> str:
    """Formats float seconds into HH:MM:SS or MM:SS format."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"
