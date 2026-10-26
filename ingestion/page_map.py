from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path


class AmbiguousPageError(ValueError):
    pass


_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def _to_roman(n: int) -> str:
    out = ""
    for value, symbol in _ROMAN:
        while n >= value:
            out += symbol
            n -= value
    return out


@dataclass(frozen=True)
class Segment:
    kind: str        # "arabic" or "roman"
    pdf_start: int   # 1-based, inclusive
    pdf_end: int     # 1-based, inclusive
    offset: int      # printed = pdf - offset

    def contains(self, pdf_page: int) -> bool:
        return self.pdf_start <= pdf_page <= self.pdf_end

    def printed(self, pdf_page: int) -> int:
        return pdf_page - self.offset


def _merge_equal(segments: list[Segment]) -> list[Segment]:
    """Join neighbours with the same kind and offset: the gap between them has that offset too."""
    merged: list[Segment] = []
    for s in segments:
        if merged and merged[-1].kind == s.kind and merged[-1].offset == s.offset:
            merged[-1] = replace(merged[-1], pdf_end=s.pdf_end)
        else:
            merged.append(s)
    return merged


class PageMap:
    def __init__(self, segments: list[Segment]):
        ordered = sorted(segments, key=lambda s: s.pdf_start)
        for a, b in zip(ordered, ordered[1:]):
            if b.pdf_start <= a.pdf_end:
                raise ValueError(f"segments overlap: {a} and {b}")
        self.segments = _merge_equal(ordered)

    def _segment_for(self, pdf_page: int) -> Segment | None:
        if pdf_page < 1:
            raise ValueError(f"pdf pages are 1-based, got {pdf_page}")
        for s in self.segments:      # ~50 segments: a linear scan is fine
            if s.contains(pdf_page):
                return s
        return None                  # front matter, back matter, or an unresolved gap

    def pdf_to_printed(self, pdf_page: int) -> int | None:
        s = self._segment_for(pdf_page)
        if s is None or s.kind != "arabic":
            return None
        printed = s.printed(pdf_page)
        return printed if printed >= 1 else None

    def pdf_to_label(self, pdf_page: int) -> str | None:
        """What to show a user: '44', or 'vi' for roman front matter."""
        s = self._segment_for(pdf_page)
        if s is None:
            return None
        printed = s.printed(pdf_page)
        if printed < 1:
            return None
        return _to_roman(printed) if s.kind == "roman" else str(printed)

    def printed_to_pdf(self, printed: int) -> int:
        matches = [printed + s.offset for s in self.segments
                   if s.kind == "arabic" and s.contains(printed + s.offset)]
        if not matches:
            raise ValueError(f"printed page {printed} not found")
        if len(matches) > 1:
            raise AmbiguousPageError(f"printed page {printed} matches pdf pages {matches}")
        return matches[0]

    @classmethod
    def from_profile(cls, profile_path: str | Path) -> "PageMap":
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        raw = profile["analyzers"]["page_numbering"]["facts"]["segments"]
        return cls([Segment(r["kind"], r["first"], r["last"], r["offset"]) for r in raw])