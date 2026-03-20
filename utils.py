"""
utils.py  —  Sigma Fetcher V10
Shared utility helpers.
"""
import html as _html
from datetime import datetime


def to_bold(text: str) -> str:
    return f"<b>{text}</b>"

def to_code(text: str) -> str:
    return f"<code>{text}</code>"

def to_italic(text: str) -> str:
    return f"<i>{text}</i>"

def safe(text: str) -> str:
    return _html.escape(str(text))

def chunks(lst: list, size: int) -> list:
    return [lst[i:i+size] for i in range(0, len(lst), size)]

def ago(dt) -> str:
    if dt is None:
        return "never"
    s = int((datetime.now() - dt).total_seconds())
    if s < 60:    return f"{s}s ago"
    if s < 3600:  return f"{s//60}m ago"
    if s < 86400: return f"{s//3600}h ago"
    return f"{s//86400}d ago"

def fmt_dt(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")
