"""Project a labelled provider enum into a scalar database text column."""


def source_enum_text(value: object) -> str | None:
    if isinstance(value,str):
        return value.strip() or None
    if isinstance(value,dict):
        for key in ('title','name','label','value'):
            label=value.get(key)
            if isinstance(label,str) and label.strip():
                return label.strip()
    # Keep opaque IDs/objects in raw JSON; do not invent a human type label.
    return None
