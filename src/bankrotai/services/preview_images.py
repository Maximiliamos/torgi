"""Pure URL extraction helpers for the desktop lot preview (BAT-308)."""

from __future__ import annotations

from urllib.parse import urlparse


def extract_preview_image_urls(raw_data: object) -> list[str]:
    """Return unique safe web images found in heterogeneous source payloads."""
    preferred_keys = (
        "image_url", "photo_url", "thumbnail_url", "main_image", "image",
        "photo", "thumbnail", "image_urls", "photo_urls", "images", "photos", "gallery",
    )

    found: list[str] = []

    def collect(value: object, *, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("//"):
                candidate = "https:" + candidate
            parsed = urlparse(candidate)
            if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
                found.append(candidate)
            return
        if isinstance(value, (list, tuple)):
            for item in value:
                collect(item, depth=depth + 1)
            return
        if isinstance(value, dict):
            lowered = {str(key).lower(): item for key, item in value.items()}
            for key in preferred_keys:
                if key in lowered:
                    collect(lowered[key], depth=depth + 1)
            for key, item in lowered.items():
                if any(token in key for token in ("image", "photo", "thumb")):
                    collect(item, depth=depth + 1)

    collect(raw_data)
    return list(dict.fromkeys(found))


def extract_preview_image_url(raw_data: object) -> str | None:
    images = extract_preview_image_urls(raw_data)
    return images[0] if images else None
