"""Parsing and formatting of timestamps like ``90``, ``01:30`` or ``00:01:30,500``."""


def parse_timestamp(text: str) -> float:
    """
    Seconds for ``SS``, ``MM:SS`` or ``HH:MM:SS``, with an optional fraction on the
    seconds written with ``.`` or SRT's ``,``. An empty string is 0.
    """
    text = text.strip().replace(",", ".")
    if not text:
        return 0.0
    *head, seconds = text.split(":")
    if len(head) > 2:
        raise ValueError(f"Invalid timestamp: '{text}'. Expected SS, MM:SS or HH:MM:SS")
    if any("." in part for part in head):
        raise ValueError(f"Only the seconds may have a fraction: '{text}'")
    total = 0.0
    for part in head:
        total = total * 60 + int(part)
    return total * 60 + float(seconds)


def format_timestamp(seconds: float, decimals: int = 0) -> str:
    """``MM:SS``, or ``HH:MM:SS`` from one hour up, with ``decimals`` digits of fractional seconds."""
    seconds = max(0.0, seconds)
    if decimals:
        whole, secs = divmod(seconds, 60)
        hours, minutes = divmod(int(whole), 60)
        secs_str = f"{secs:0{decimals + 3}.{decimals}f}"
    else:
        hours, remainder = divmod(int(round(seconds)), 3600)
        minutes, secs = divmod(remainder, 60)
        secs_str = f"{secs:02d}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs_str}"
    return f"{minutes:02d}:{secs_str}"
