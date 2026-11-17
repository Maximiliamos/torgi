"""Bounded extraction of one complete primary view from oversized M-ETS HTML."""
from __future__ import annotations

from html.parser import HTMLParser


class ExactMetsView(HTMLParser):
    """Retain one view at a time; recommendations under noindex are excluded."""

    def __init__(self, sku: str, limit: int) -> None:
        super().__init__(convert_charrefs=False)
        self.sku = sku
        self.limit = limit
        self.depth = 0
        self.noindex = 0
        self.start_depth: int | None = None
        self.parts: list[str] = []
        self.size = 0
        self.matches = 0
        self.invalid = False
        self.result: str | None = None
        self.saw_primary_view = False

    def _append(self, text: str) -> None:
        if self.start_depth is None or self.invalid:
            return
        self.size += len(text.encode("utf-8"))
        if self.size > self.limit:
            self.invalid = True
            self.parts.clear()
        else:
            self.parts.append(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "noindex":
            self.noindex += 1
        if tag == "div":
            self.depth += 1
            if "generalview-container" in (values.get("class") or "").split():
                self.saw_primary_view = True
                if self.start_depth is not None:
                    self.invalid = True
                elif not self.noindex:
                    self.start_depth = self.depth
                    self.parts = []
                    self.size = self.matches = 0
                    self.invalid = False
        self._append(self.get_starttag_text() or "")
        if values.get("itemprop") == "sku" and values.get("content") == self.sku:
            if self.start_depth is not None and not self.noindex:
                self.matches += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in {"div", "noindex"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self._append(f"</{tag}>")
        if tag == "div":
            if self.start_depth == self.depth:
                if self.matches == 1 and not self.invalid:
                    self.result = "".join(self.parts)
                self.start_depth = None
                self.parts = []
            self.depth = max(0, self.depth - 1)
        if tag == "noindex":
            self.noindex = max(0, self.noindex - 1)

    def handle_data(self, data):
        self._append(data)

    def handle_entityref(self, name):
        self._append(f"&{name};")

    def handle_charref(self, name):
        self._append(f"&#{name};")

    def handle_comment(self, data):
        self._append(f"<!--{data}-->")
