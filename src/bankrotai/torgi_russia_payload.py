"""Decode public Next.js payload text without provider or database side effects."""
import json
import re

from bs4 import BeautifulSoup


def extract_next_flight_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    parts: list[str] = []
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        match = re.fullmatch(r"\s*self\.__next_f\.push\((.*)\)\s*", script_text, flags=re.DOTALL)
        if match is None:
            continue
        try:
            payload = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], str):
            parts.append(payload[1])
    return "".join(parts)


def extract_named_json_value(text: str, key: str):
    marker = f'"{key}":'
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    try:
        value, _end = json.JSONDecoder().raw_decode(text, start)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value
