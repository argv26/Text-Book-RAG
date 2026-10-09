#!/usr/bin/env python3
r"""
text-book-analyser.py
=====================

Analyses textbook PDFs and produces a human-readable report (report.md) plus a
machine-readable profile (profile.json) that a RAG ingestion pipeline can be
built from.  Nothing is hard-coded for one book: every finding is derived from
the PDF itself, and the outline (bookmarks) is used as ground truth where it
exists.

Folder layout (relative to this file)
-------------------------------------
    test-books\            put the PDFs to analyse here
    analysis-result\       output, one sub-folder per book:
                               report.md      human readable report
                               profile.json   same data, machine readable
                               samples\       line-level dumps of evidence pages
                           index.md           one-line summary per analysed book

Usage (PowerShell, virtual env active)
--------------------------------------
    python text-book-analyser.py                        # every PDF in test-books
    python text-book-analyser.py my-book.pdf            # one book (name or path)
    python text-book-analyser.py --max-pages 150        # quick partial run
    python text-book-analyser.py --deep-tables          # run table detection widely
    python text-book-analyser.py --config my-config.json
    python text-book-analyser.py --list-analyzers

How to extend (Open/Closed principle)
-------------------------------------
* New check      : subclass `Analyzer`, set `key`, `title`, `requires`, implement
                   `run(ctx) -> AnalysisResult`, decorate with `@register`.
* New book type  : add `SectionRule`s (config JSON "section_rules") instead of
                   changing code.
* New output     : implement `Renderer` and add it to `RENDERERS`.
Analyzers never print or write files; they only return `AnalysisResult`s, and
renderers turn those into files.  Analyzers share data through `BookContext`.
"""
from __future__ import annotations

import argparse
import bisect
import difflib
import json
import math
import re
import statistics
import sys
import time
import traceback
import unicodedata
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Iterable, Optional
from urllib.parse import urlparse

import pymupdf

ANALYSER_VERSION = "1.0"
ROOT = Path(__file__).resolve().parent
BOOKS_DIR = ROOT / "test-books"
RESULTS_DIR = ROOT / "analysis-result"


# ======================================================================
# Utilities
# ======================================================================
def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def alnum_key(s: str) -> str:
    """Lower-case letters and digits only; used for fuzzy title comparison."""
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def norm_digits(s: str) -> str:
    """Replace digit runs by '#', so '1.5 Homeostasis 21' ~ '#.# Homeostasis #'."""
    return re.sub(r"\d+", "#", norm_ws(s).lower())


def clip(s: str, n: int = 60) -> str:
    s = norm_ws(s)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def md_cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def pct(a: float, b: float, digits: int = 1) -> str:
    return "n/a" if not b else f"{100.0 * a / b:.{digits}f}%"


def ratio(a: float, b: float) -> float:
    return 0.0 if not b else a / b


def percentile(values: list, q: float):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[int(k)] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def median(values: list):
    return statistics.median(values) if values else None


def compress_ranges(nums: Iterable[int], limit: int = 60) -> str:
    nums = sorted(set(nums))
    ranges: list[list[int]] = []
    for n in nums:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    parts = [f"{a}-{b}" if a != b else str(a) for a, b in ranges]
    if not parts:
        return "-"
    if len(parts) > limit:
        return ", ".join(parts[:limit]) + f", ... (+{len(parts) - limit} more ranges)"
    return ", ".join(parts)


def hex_color(c) -> Optional[str]:
    """PyMuPDF colour (int sRGB, or tuple of 1/3/4 floats) -> '#rrggbb'."""
    if c is None:
        return None
    try:
        if isinstance(c, int):
            return f"#{c & 0xFFFFFF:06x}"
        c = tuple(c)
        if len(c) == 1:
            v = int(round(max(0, min(1, c[0])) * 255))
            return f"#{v:02x}{v:02x}{v:02x}"
        if len(c) == 3:
            r, g, b = (int(round(max(0, min(1, x)) * 255)) for x in c)
            return f"#{r:02x}{g:02x}{b:02x}"
        if len(c) == 4:
            cc, m, y, k = c
            r = int(round(255 * (1 - cc) * (1 - k)))
            g = int(round(255 * (1 - m) * (1 - k)))
            b = int(round(255 * (1 - y) * (1 - k)))
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return None
    return None


_ROMAN_RE = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)


def roman_to_int(s: str) -> Optional[int]:
    s = (s or "").strip().lower()
    if not s or len(s) > 7 or not _ROMAN_RE.match(s):
        return None
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


def est_tokens(chars: int) -> int:
    """Rough token estimate (English text ~ 4 characters per token)."""
    return int(chars / 4)


# ======================================================================
# Configuration (all thresholds live here; override with --config file.json)
# ======================================================================
@dataclass
class SectionRule:
    """Maps an outline title to a section kind and the way a RAG should use it."""
    name: str
    pattern: str
    use: str                      # teaching | container | glossary | summary | quiz_source |
                                  # quiz_answers | reference | skip | metadata
    description: str = ""


DEFAULT_SECTION_RULES = [
    SectionRule("contents", r"^(table of )?contents?$", "skip", "table of contents"),
    SectionRule("preface", r"^(preface|foreword|about (this book|the (authors?|book)|openstax)|acknowledg(e)?ments?|dedication|how to use)", "skip", "front matter"),
    SectionRule("index", r"^(subject |author |general )?index$", "skip", "back-of-book index"),
    SectionRule("references", r"^(references|bibliography|works cited|further reading|sources|credits|image credits)$", "reference", "reference lists"),
    SectionRule("answer_key", r"^(answer (key|section)s?|answers?( to .*)?|solutions?( manual)?|selected answers)$", "quiz_answers", "answers to exercises"),
    SectionRule("appendix", r"^appendi(x|ces)\b", "reference", "appendix"),
    SectionRule("glossary", r"^glossary\b", "glossary", "glossary"),
    SectionRule("key_terms", r"^(key )?terms( and concepts)?$|^key vocabulary|^vocabulary|^terminology", "glossary", "term/definition list"),
    SectionRule("chapter_objectives", r"^(chapter |learning |unit )?objectives?$|^(chapter )?learning (goals|outcomes)", "metadata", "learning objectives"),
    SectionRule("chapter_review", r"^(chapter )?(review|summary)$|^chapter summary|^key (points|concepts|takeaways)$|^in summary", "summary", "end-of-chapter summary"),
    SectionRule("interactive_link_questions", r"^interactive link", "quiz_source", "questions about linked media"),
    SectionRule("review_questions", r"^((review|self[- ]review|study|practice|multiple[- ]choice|chapter) )?(questions|problems|exercises|quiz)$|^check your understanding|^self[- ]test|^test yourself", "quiz_source", "end-of-chapter questions"),
    SectionRule("critical_thinking", r"^critical thinking|^discussion questions|^application questions|^case (studies|study)", "quiz_source", "open-ended questions"),
    SectionRule("introduction", r"^(introduction|overview|chapter introduction)$", "teaching", "chapter introduction"),
    SectionRule("chapter", r"^(chapter|unit|part|module|lesson)\s+(\d+|[ivxlcdm]+)\b", "container", "chapter/unit container"),
    SectionRule("numbered_section", r"^\d+(\.\d+)+\b", "teaching", "numbered teaching section"),
]


@dataclass
class AnalyzerConfig:
    max_pages: int = 0                      # 0 = analyse every page
    dump_samples: bool = True
    max_samples: int = 40
    deep_tables: bool = False
    max_table_pages: int = 250
    band_scan_ratio: float = 0.15           # top/bottom share of page scanned for running lines
    band_pad_pt: float = 2.0
    running_min_coverage: float = 0.25      # a running header/footer appears on >= this share of pages
    heading_match_ratio: float = 0.85       # fuzzy match threshold outline title <-> page line
    mid_page_threshold_pt: float = 30.0     # section "starts mid-page" if heading is this far below the first body line
    column_min_lines_per_side: int = 8
    column_max_crossing: float = 0.12
    box_label_min_count: int = 3
    box_label_max_words: int = 6
    box_label_sample_per_type: int = 40
    caption_pattern: str = (r"^\s*(figure|fig\.|table|chart|box|exhibit|plate|illustration)\s*"
                            r"[A-Z]?\d+([.\-\u2013]\d+)*[A-Za-z]?\b")
    table_rows_in_report: int = 25
    examples_per_item: int = 3
    section_rules: list = field(default_factory=lambda: list(DEFAULT_SECTION_RULES))

    @classmethod
    def from_json(cls, path: Path) -> "AnalyzerConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = cls()
        names = {f.name for f in fields(cls)}
        for k, v in raw.items():
            if k == "section_rules":
                cfg.section_rules = [SectionRule(**r) for r in v]
            elif k in names:
                setattr(cfg, k, v)
        return cfg


# ======================================================================
# Data model
# ======================================================================
@dataclass(frozen=True)
class Style:
    font: str
    size: float
    bold: bool
    italic: bool
    color: str

    @property
    def family(self) -> str:
        return re.split(r"[-,]", self.font)[0]

    def label(self) -> str:
        w = ("bold " if self.bold else "") + ("italic " if self.italic else "")
        return f"{self.font} {self.size:g}pt {w}{self.color}".replace("  ", " ")


class StyleTable:
    """Interns Style objects so lines/spans only store small integer ids."""

    def __init__(self):
        self._ids: dict[Style, int] = {}
        self._styles: list[Style] = []

    def intern(self, style: Style) -> int:
        sid = self._ids.get(style)
        if sid is None:
            sid = len(self._styles)
            self._ids[style] = sid
            self._styles.append(style)
        return sid

    def get(self, sid: int) -> Style:
        return self._styles[sid]

    def __len__(self):
        return len(self._styles)


_BOLD_NAME = re.compile(r"(bold|black|heavy|semibold|demi|extrabold)", re.I)
_ITALIC_NAME = re.compile(r"(italic|oblique)", re.I)
_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


@dataclass
class Span:
    text: str
    style: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Line:
    page: int
    block: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    spans: list
    dom: int            # dominant style id (by characters)
    dom_share: float    # share of characters in the dominant style
    vertical: bool = False

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def pure(self) -> bool:
        return self.dom_share >= 0.95


@dataclass
class ImageRec:
    x0: float
    y0: float
    x1: float
    y1: float
    xref: int
    px_w: int
    px_h: int

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return max(0.0, self.w) * max(0.0, self.h)


@dataclass
class Shape:
    kind: str
    fill: Optional[str]
    stroke: Optional[str]
    opacity: Optional[float]
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return max(0.0, self.w) * max(0.0, self.h)


@dataclass
class PageData:
    number: int
    width: float
    height: float
    rotation: int
    lines: list
    images: list
    chars: int
    image_ratio: float


@dataclass
class Entry:
    """One outline (bookmark) entry, enriched by the analysers."""
    index: int
    level: int
    title: str
    page: int
    source: str = "outline"
    parent: Optional[int] = None
    next_start_page: int = 0
    kind: str = ""
    use: str = ""


class SectionClassifier:
    def __init__(self, rules: list):
        self._rules = [(r, re.compile(r.pattern, re.I)) for r in rules]

    def classify(self, title: str, level: int = 2) -> tuple[str, str]:
        t = norm_ws(title)
        for rule, rx in self._rules:
            if rx.search(t):
                return rule.name, rule.use
        return ("top_level_other", "review") if level <= 1 else ("section", "teaching")


# ======================================================================
# Results
# ======================================================================
@dataclass
class Table:
    title: str
    headers: list
    rows: list
    note: str = ""


@dataclass
class Finding:
    severity: str        # info | warning | problem
    message: str
    analyzer: str = ""


@dataclass
class AnalysisResult:
    key: str
    title: str
    summary: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    tables: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    recipe: dict = field(default_factory=dict)
    recipe_notes: list = field(default_factory=list)
    scorecard: list = field(default_factory=list)     # (label, status, detail)
    seconds: float = 0.0
    failed: bool = False

    def say(self, text: str):
        self.summary.append(text)

    def info(self, msg: str):
        self.findings.append(Finding("info", msg, self.key))

    def warn(self, msg: str):
        self.findings.append(Finding("warning", msg, self.key))

    def problem(self, msg: str):
        self.findings.append(Finding("problem", msg, self.key))

    def score(self, label: str, status: str, detail: str):
        self.scorecard.append((label, status, detail))

    def note(self, text: str):
        self.recipe_notes.append(text)

    def add_table(self, title, headers, rows, note=""):
        self.tables.append(Table(title, headers, rows, note))


# ======================================================================
# Book context: cached, lazily computed views of the PDF shared by analysers
# ======================================================================
class BookContext:
    def __init__(self, path: Path, config: AnalyzerConfig):
        self.path = Path(path)
        self.config = config
        self.doc = pymupdf.open(str(path))
        self.styles = StyleTable()
        self.pages: list[PageData] = []
        self.total_pages = self.doc.page_count
        self.results: dict[str, AnalysisResult] = {}
        self.shared: dict[str, Any] = {}
        self.evidence: dict[str, list] = defaultdict(list)
        self.classifier = SectionClassifier(config.section_rules)
        self.caption_re = re.compile(config.caption_pattern, re.I)
        self.entries: list[Entry] = []
        self.header_ratio = 0.0     # lines with y1 <= ratio * page height are running headers
        self.footer_ratio = 1.0     # lines with y0 >= ratio * page height are running footers
        self.has_bands = False
        self._drawings: dict[int, list] = {}
        self._body_sid: Optional[int] = None
        self._captions: Optional[list] = None

    # ---- loading -----------------------------------------------------
    def load(self, progress=True):
        limit = self.config.max_pages or self.total_pages
        limit = min(limit, self.total_pages)
        t0 = time.time()
        for i in range(limit):
            self.pages.append(self._extract_page(i))
            if progress and (i + 1) % 200 == 0:
                print(f"    read {i + 1}/{limit} pages ({time.time() - t0:.0f}s)")

    def _style_of(self, span: dict) -> int:
        font = _SUBSET_PREFIX.sub("", span.get("font", "") or "unknown")
        flags = span.get("flags", 0)
        bold = bool(flags & 16) or bool(_BOLD_NAME.search(font))
        italic = bool(flags & 2) or bool(_ITALIC_NAME.search(font))
        return self.styles.intern(Style(font, round(float(span.get("size", 0)), 1), bold, italic,
                                        hex_color(span.get("color", 0)) or "#000000"))

    def _extract_page(self, idx: int) -> PageData:
        page = self.doc[idx]
        W, H = page.rect.width, page.rect.height
        lines: list[Line] = []
        chars = 0
        try:
            blocks = page.get_text("dict")["blocks"]
        except Exception:
            blocks = []
        for bi, b in enumerate(blocks):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                spans, per_style = [], Counter()
                raw = []
                for s in ln.get("spans", []):
                    raw.append(s.get("text", ""))
                    t = s.get("text", "")
                    if not t.strip():
                        continue
                    sid = self._style_of(s)
                    bb = s.get("bbox", ln["bbox"])
                    spans.append(Span(t, sid, bb[0], bb[1], bb[2], bb[3]))
                    per_style[sid] += len(t.strip())
                text = norm_ws("".join(raw))
                if not text or not spans:
                    continue
                dom, dom_n = per_style.most_common(1)[0]
                total = sum(per_style.values())
                d = ln.get("dir", (1, 0))
                vertical = abs(d[1]) > 0.3
                bb = ln["bbox"]
                lines.append(Line(idx + 1, bi, bb[0], bb[1], bb[2], bb[3], text, spans, dom,
                                  dom_n / total if total else 1.0, vertical))
                chars += len(text)
        images: list[ImageRec] = []
        try:
            for info in page.get_image_info(xrefs=True):
                bb = info["bbox"]
                images.append(ImageRec(bb[0], bb[1], bb[2], bb[3], int(info.get("xref", 0) or 0),
                                       int(info.get("width", 0) or 0), int(info.get("height", 0) or 0)))
        except Exception:
            pass
        page_area = max(1.0, W * H)
        img_area = sum(min(i.area, page_area) for i in images)
        return PageData(idx + 1, W, H, page.rotation, lines, images, chars, min(1.0, img_area / page_area))

    # ---- accessors ---------------------------------------------------
    def page(self, n: int) -> PageData:
        return self.pages[n - 1]

    def band_of(self, ln: Line, p: Optional[PageData] = None) -> Optional[str]:
        p = p or self.pages[ln.page - 1]
        if self.header_ratio > 0 and ln.y1 <= p.height * self.header_ratio + 0.5:
            return "header"
        if self.footer_ratio < 1 and ln.y0 >= p.height * self.footer_ratio - 0.5:
            return "footer"
        return None

    def body_lines(self, n: int) -> list:
        p = self.pages[n - 1]
        return [ln for ln in p.lines if not self.band_of(ln, p)]

    @property
    def body_style_id(self) -> int:
        """Most common style among long lines (the running text)."""
        if self._body_sid is None:
            c = Counter()
            for p in self.pages:
                for ln in p.lines:
                    if len(ln.text) >= 40 and not ln.vertical and not self.band_of(ln, p):
                        c[ln.dom] += len(ln.text)
            if not c:
                for p in self.pages:
                    for ln in p.lines:
                        c[ln.dom] += len(ln.text)
            self._body_sid = c.most_common(1)[0][0] if c else self.styles.intern(
                Style("unknown", 10.0, False, False, "#000000"))
        return self._body_sid

    @property
    def body_style(self) -> Style:
        return self.styles.get(self.body_style_id)

    def drawings(self, n: int) -> list:
        """Vector shapes of a page (lazy; skips specks, caps the count)."""
        if n in self._drawings:
            return self._drawings[n]
        shapes: list[Shape] = []
        try:
            raw = self.doc[n - 1].get_drawings()
        except Exception:
            raw = []
        for d in raw:
            r = d["rect"]
            if max(r.width, r.height) < 3:
                continue
            shapes.append(Shape(d.get("type", "?"), hex_color(d.get("fill")), hex_color(d.get("color")),
                                d.get("fill_opacity"), r.x0, r.y0, r.x1, r.y1))
            if len(shapes) >= 1500:
                break
        self._drawings[n] = shapes
        return shapes

    @property
    def captions(self) -> list:
        """Lines that look like figure/table captions: (page, Line, kind, label)."""
        if self._captions is None:
            out = []
            bsid = self.body_style_id
            bsize = self.styles.get(bsid).size
            for p in self.pages:
                for ln in self.body_lines(p.number):
                    m = self.caption_re.match(ln.text)
                    if not m:
                        continue
                    first = ln.spans[0].style
                    if first == bsid and ln.dom == bsid:
                        continue        # a body sentence that merely starts with "Figure 3 shows"
                    kind = "figure" if m.group(1).lower().startswith("fig") else m.group(1).lower()
                    out.append((p.number, ln, kind, norm_ws(m.group(0))))
            self._captions = out
        return self._captions

    def add_evidence(self, reason: str, page: int, keep: int = 3):
        if page and 1 <= page <= len(self.pages) and page not in self.evidence[reason] \
                and len(self.evidence[reason]) < keep:
            self.evidence[reason].append(page)

    def entry_page_range(self, e: Entry) -> range:
        end = e.next_start_page if e.next_start_page >= e.page else e.page
        return range(e.page, min(end, len(self.pages)) + 1)

    def kind_of_page(self, n: int) -> str:
        idx = self.shared.get("page_entry", {}).get(n)
        if idx is None or idx >= len(self.entries):
            return "(unknown)"
        return self.entries[idx].kind


# ======================================================================
# Analyzer base class and registry
# ======================================================================
class Analyzer(ABC):
    key: ClassVar[str] = ""
    title: ClassVar[str] = ""
    requires: ClassVar[tuple] = ()

    def new_result(self) -> AnalysisResult:
        return AnalysisResult(self.key, self.title)

    @abstractmethod
    def run(self, ctx: BookContext) -> AnalysisResult:
        ...


_REGISTRY: list = []


def register(cls):
    _REGISTRY.append(cls)
    return cls


def resolve_order(classes: list) -> list:
    by_key = {c.key: c for c in classes}
    done, order = set(), []

    def visit(c, stack=()):
        if c.key in done:
            return
        for r in c.requires:
            if r in by_key and r not in stack:
                visit(by_key[r], stack + (c.key,))
        done.add(c.key)
        order.append(c)

    for c in classes:
        visit(c)
    return order


def run_analyzers(ctx: BookContext, classes: list) -> list:
    results = []
    for cls in resolve_order(classes):
        t0 = time.time()
        print(f"  - {cls.title} ...", end="", flush=True)
        try:
            res = cls().run(ctx)
        except Exception as exc:                     # one failing analyser must not stop the run
            res = AnalysisResult(cls.key, cls.title, failed=True)
            res.problem(f"Analyser crashed: {exc!r}. See 'error' in profile.json.")
            res.facts["error"] = traceback.format_exc()
            res.score(cls.title, "fail", "analyser crashed")
        res.seconds = round(time.time() - t0, 2)
        ctx.results[cls.key] = res
        results.append(res)
        print(f" {res.seconds:.1f}s")
    return results



# ======================================================================
# Analyzer: document overview
# ======================================================================
@register
class OverviewAnalyzer(Analyzer):
    key = "overview"
    title = "Document overview"

    def run(self, ctx):
        r = self.new_result()
        doc, pages = ctx.doc, ctx.pages
        n = len(pages)
        md = doc.metadata or {}
        sizes = Counter((round(p.width), round(p.height)) for p in pages)
        rot = Counter(p.rotation for p in pages)
        no_text = [p.number for p in pages if p.chars < 20]
        scanned = [p.number for p in pages if p.chars < 50 and p.image_ratio > 0.6]
        blank = [p.number for p in pages if p.chars < 20 and p.image_ratio < 0.02]
        total_chars = sum(p.chars for p in pages)
        n_lines = sum(len(p.lines) for p in pages)
        vertical = sum(1 for p in pages for ln in p.lines if ln.vertical)
        n_images = sum(len(p.images) for p in pages)
        text_pages = n - len(no_text)

        r.facts = {
            "file": ctx.path.name, "file_size_mb": round(ctx.path.stat().st_size / 1e6, 2),
            "pages_total": ctx.total_pages, "pages_analysed": n,
            "metadata": {k: v for k, v in md.items() if v},
            "encrypted": bool(doc.is_encrypted), "page_sizes": {f"{w}x{h}": c for (w, h), c in sizes.items()},
            "rotations": dict(rot), "pages_without_text": len(no_text), "scanned_like_pages": len(scanned),
            "blank_pages": len(blank), "text_chars": total_chars, "est_tokens": est_tokens(total_chars),
            "text_lines": n_lines, "non_horizontal_lines": vertical, "images_total": n_images,
            "styles_found": len(ctx.styles),
        }
        r.say(f"{ctx.total_pages} pages ({n} analysed), {total_chars:,} characters of text "
              f"(about {est_tokens(total_chars):,} tokens), {n_images:,} placed images, "
              f"{len(ctx.styles)} distinct text styles.")
        if n < ctx.total_pages:
            r.warn(f"PARTIAL ANALYSIS: only the first {n} of {ctx.total_pages} pages were analysed (--max-pages).")
        r.add_table("File and metadata", ["Property", "Value"],
                    [["File", ctx.path.name], ["Size (MB)", r.facts["file_size_mb"]],
                     ["Pages", ctx.total_pages]]
                    + [[k, clip(str(v), 80)] for k, v in r.facts["metadata"].items()])
        r.add_table("Page sizes (points)", ["Size", "Pages"], [[k, v] for k, v in
                                                              sorted(r.facts["page_sizes"].items(), key=lambda kv: -kv[1])])
        if doc.is_encrypted:
            r.problem("The PDF is encrypted; text extraction may be restricted.")
        if vertical:
            r.info(f"{vertical} text lines are rotated (not horizontal): landscape tables or margin text. "
                   "They are excluded from most statistics.")
        if len(sizes) > 1:
            r.info(f"The book mixes {len(sizes)} page sizes; position-based rules must use ratios, not absolute points.")
        ratio_text = ratio(text_pages, n)
        if ratio_text >= 0.9 and not scanned:
            r.score("Text layer", "ok", f"{pct(text_pages, n)} of pages have selectable text")
        elif len(scanned) > 0.05 * n:
            r.score("Text layer", "fail", f"{len(scanned)} pages look scanned (image only): OCR is required")
            r.problem(f"{len(scanned)} pages are image-only (scanned). Run OCR before ingestion "
                      f"(e.g. ocrmypdf) - first pages: {compress_ranges(scanned, 8)}")
        else:
            r.score("Text layer", "warn", f"{pct(text_pages, n)} of pages have selectable text")
        if no_text:
            r.info(f"Pages with (almost) no text: {compress_ranges(no_text)}")
        r.recipe = {"needs_ocr": len(scanned) > 0.05 * n, "scanned_pages": scanned[:200]}
        if scanned:
            r.note(f"OCR needed for {len(scanned)} scanned pages.")
        if text_pages:
            ctx.add_evidence("first_text_page", next(p.number for p in pages if p.chars >= 20))
        return r


# ======================================================================
# Analyzer: running headers / footers
# ======================================================================
@register
class HeaderFooterAnalyzer(Analyzer):
    key = "header_footer"
    title = "Running headers, footers and page-number bands"
    requires = ("overview",)

    def run(self, ctx):
        r = self.new_result()
        cfg = ctx.config
        pages = [p for p in ctx.pages if p.chars >= 20]
        if not pages:
            r.warn("No text pages; header/footer detection skipped.")
            return r
        body = ctx.body_style_id
        groups = defaultdict(list)
        for p in pages:
            H = p.height
            for ln in p.lines:
                if ln.vertical:
                    continue
                if ln.y0 < cfg.band_scan_ratio * H:
                    side = "top"
                elif ln.y1 > (1 - cfg.band_scan_ratio) * H:
                    side = "bottom"
                else:
                    continue
                groups[(side, round(ln.y0 / 3), ln.dom)].append((p.number, ln))
        npages = len(pages)
        odd_total = sum(1 for p in pages if p.number % 2)
        even_total = npages - odd_total
        running = []
        for (side, _yb, sid), items in groups.items():
            pnums = {pn for pn, _ in items}
            cov = len(pnums) / npages
            if cov < cfg.running_min_coverage:
                continue
            texts = Counter(norm_digits(ln.text) for _, ln in items)
            numeric = sum(1 for _, ln in items if re.fullmatch(r"[\divxlcdm]{1,6}", ln.text.strip(), re.I)) / len(items)
            top3 = sum(c for _, c in texts.most_common(3)) / len(items)
            if sid == body and numeric < 0.5:
                continue            # body text that merely starts at a constant y
            if ctx.styles.get(sid).size > ctx.body_style.size * 1.05 and numeric < 0.5:
                continue            # running heads are never larger than body text; larger lines are headings
            odd = sum(1 for x in pnums if x % 2)
            running.append({
                "side": side, "sid": sid, "coverage": cov,
                "odd": ratio(odd, odd_total), "even": ratio(len(pnums) - odd, even_total),
                "y": median([ln.y0 for _, ln in items]), "x": median([ln.x0 for _, ln in items]),
                "y1max": percentile([ln.y1 for _, ln in items], 0.98),
                "y0min": percentile([ln.y0 for _, ln in items], 0.02),
                "numeric": numeric, "examples": [t for t, _ in texts.most_common(3)],
            })
        running.sort(key=lambda x: (x["side"] != "top", x["y"], x["x"]))
        Hs = median([p.height for p in pages]) or 792.0
        tops = [x for x in running if x["side"] == "top"]
        bots = [x for x in running if x["side"] == "bottom"]
        if tops:
            ctx.header_ratio = (max(x["y1max"] for x in tops) + cfg.band_pad_pt) / Hs
        if bots:
            ctx.footer_ratio = (min(x["y0min"] for x in bots) - cfg.band_pad_pt) / Hs
        ctx.has_bands = bool(running)

        header_cut = round(ctx.header_ratio * Hs, 1) if tops else None
        footer_cut = round(ctx.footer_ratio * Hs, 1) if bots else None
        r.facts = {
            "running_lines": [{
                "side": x["side"], "y": round(x["y"], 1), "x": round(x["x"], 1),
                "style": ctx.styles.get(x["sid"]).label(), "coverage": round(x["coverage"], 3),
                "odd_pages": round(x["odd"], 3), "even_pages": round(x["even"], 3),
                "numeric_share": round(x["numeric"], 3), "examples": x["examples"]} for x in running],
            "header_band_bottom_pt": header_cut, "footer_band_top_pt": footer_cut,
            "page_height_used": Hs,
        }
        if running:
            r.say(f"Found {len(tops)} running header line type(s) and {len(bots)} running footer line type(s). "
                  f"Header band ends at y~{header_cut}pt, footer band starts at y~{footer_cut}pt "
                  f"(page height {Hs:.0f}pt). Lines inside these bands are page furniture and must be dropped.")
            r.add_table("Running header/footer line types",
                        ["Side", "y", "x", "Style", "Pages", "Odd pages", "Even pages", "Numeric", "Typical text (digits as #)"],
                        [[x["side"], f"{x['y']:.0f}", f"{x['x']:.0f}", ctx.styles.get(x["sid"]).label(),
                          pct(x["coverage"], 1, 0), pct(x["odd"], 1, 0), pct(x["even"], 1, 0),
                          pct(x["numeric"], 1, 0), "; ".join(clip(t, 40) for t in x["examples"])] for x in running])
            if any(abs(x["odd"] - x["even"]) > 0.4 for x in running):
                r.info("Odd and even pages have different header/footer layouts (recto/verso). "
                       "Detect page numbers on either side of the page.")
            if ctx.header_ratio > 0.12:
                r.warn(f"Header band is unusually tall ({header_cut}pt); check that body text is not being dropped.")
            r.score("Headers/footers", "ok", f"{len(running)} running line types, bands detected")
        else:
            r.say("No running headers or footers detected (no line repeats at a fixed position on >= "
                  f"{int(cfg.running_min_coverage * 100)}% of pages).")
            r.score("Headers/footers", "info", "none detected")
        r.recipe = {"header_band_bottom_pt": header_cut, "footer_band_top_pt": footer_cut,
                    "strip_lines_inside_bands": bool(running)}
        r.note(f"Drop every line with y1 <= {header_cut}pt (header) or y0 >= {footer_cut}pt (footer)."
               if running else "No header/footer stripping needed.")
        return r


# ======================================================================
# Analyzer: printed page numbers vs PDF page numbers
# ======================================================================
_NUM_SPAN = re.compile(r"^(?:page\s*)?[-\u2013\u2014\[(]?\s*(\d{1,4})\s*[-\u2013\u2014\])]?$", re.I)


@register
class PageNumberingAnalyzer(Analyzer):
    key = "page_numbering"
    title = "Printed page numbers"
    requires = ("header_footer",)

    @staticmethod
    def candidates(ctx, p):
        H = p.height
        out = []
        for ln in p.lines:
            if ln.vertical:
                continue
            in_band = ctx.band_of(ln, p) is not None if ctx.has_bands else (ln.y1 <= 0.07 * H or ln.y0 >= 0.93 * H)
            if not in_band:
                continue
            for sp in ln.spans:
                t = sp.text.strip()
                m = _NUM_SPAN.match(t)
                if m and int(m.group(1)) <= 5000:
                    out.append(("arabic", int(m.group(1)), sp))
                elif t.isalpha() and len(t) <= 6 and (t.islower() or t.isupper()):
                    v = roman_to_int(t)
                    if v:
                        out.append(("roman", v, sp))
        return out

    def run(self, ctx):
        r = self.new_result()
        pages = ctx.pages
        chosen = {}
        for p in pages:
            c = self.candidates(ctx, p)
            if c:
                kind, val, sp = min(c, key=lambda t: min(t[2].x0, p.width - t[2].x1))
                chosen[p.number] = (kind, val, sp.x0, sp.x1, p.width)
        text_pages = [p for p in pages if p.chars >= 20]
        text_set = {p.number for p in text_pages}
        numbered_text = sum(1 for pn in chosen if pn in text_set)
        arabic_offsets = Counter(pn - v for pn, (k, v, *_x) in chosen.items() if k == "arabic")
        dominant = arabic_offsets.most_common(1)[0][0] if arabic_offsets else None
        agree = arabic_offsets[dominant] if dominant is not None else 0
        n_arabic = sum(arabic_offsets.values())

        segments = []
        for pn in sorted(chosen):
            kind, val = chosen[pn][0], chosen[pn][1]
            off = pn - val
            if segments and segments[-1]["kind"] == kind and segments[-1]["offset"] == off:
                segments[-1]["last"] = pn
                segments[-1]["count"] += 1
            else:
                segments.append({"kind": kind, "offset": off, "first": pn, "last": pn, "count": 1})
        major = [s for s in segments if s["count"] >= 3]
        unnumbered = [p.number for p in text_pages if p.number not in chosen]

        label_sample = []
        try:
            idxs = sorted(set(list(range(min(30, len(pages)))) + list(range(0, len(pages), max(1, len(pages) // 20)))))
            label_sample = [(i + 1, ctx.doc[i].get_label()) for i in idxs]
        except Exception:
            pass
        labels_defined = any(lbl for _, lbl in label_sample)

        side = {}
        for parity, name in ((1, "odd"), (0, "even")):
            xs = [(v[2] + v[3]) / 2 / v[4] for pn, v in chosen.items() if pn % 2 == parity]
            if xs:
                m = median(xs)
                side[name] = "left" if m < 0.33 else "right" if m > 0.67 else "centre"
        ctx.shared["page_numbers"] = chosen
        ctx.shared["dominant_offset"] = dominant
        ctx.shared["unnumbered_pages"] = unnumbered

        r.facts = {
            "pdf_page_labels_defined": labels_defined, "label_sample": label_sample[:12],
            "numbered_pages": len(chosen), "text_pages": len(text_pages),
            "dominant_arabic_offset": dominant, "offset_agreement": round(ratio(agree, n_arabic), 4),
            "segments": segments, "unnumbered_pages": unnumbered, "number_side_by_parity": side,
        }
        if dominant is None:
            r.say("No printed page numbers were found in header/footer bands. Citations will use PDF page numbers.")
            r.score("Page numbers", "info", "no printed numbers; use PDF page numbers")
        else:
            r.say(f"Printed page numbers were read on {len(chosen)} pages ({numbered_text} of the {len(text_pages)} pages with text). "
                  f"Arabic numbering: printed = PDF page {-dominant:+d} for {pct(agree, n_arabic)} of numbered pages "
                  f"(i.e. PDF page = printed + {dominant}). Number position: {side or 'unknown'}.")
            if labels_defined:
                r.say("The PDF also defines page labels, which can be used directly.")
            r.add_table("Numbering segments (PDF page = printed + offset)",
                        ["Kind", "Offset", "First PDF page", "Last PDF page", "Pages numbered"],
                        [[s["kind"], s["offset"], s["first"], s["last"], s["count"]] for s in major[:40]])
            rate = ratio(agree, n_arabic)
            if rate >= 0.95:
                r.score("Page numbers", "ok", f"constant offset {dominant} on {pct(agree, n_arabic)} of numbered pages")
            else:
                r.score("Page numbers", "warn", f"offset varies ({len(major)} segments); map per segment")
                r.warn("The page-number offset is not constant; use the segment table to convert printed <-> PDF pages.")
        r.say(f"{len(unnumbered)} text pages show no printed number: {compress_ranges(unnumbered, 25)}. "
              "Section 'Page coverage' explains which part of the book they belong to.")
        r.recipe = {"pdf_page_equals_printed_plus": dominant, "segments": major[:60],
                    "use_pdf_labels": labels_defined, "number_side_by_parity": side}
        r.note(f"Convert printed page -> PDF page by adding {dominant}." if dominant is not None
               else "No printed numbers: cite PDF page numbers.")
        return r


# ======================================================================
# Analyzer: printed table of contents (fallback when no outline exists)
# ======================================================================
@register
class PrintedTocAnalyzer(Analyzer):
    key = "printed_toc"
    title = "Printed table of contents"
    requires = ("page_numbering",)

    @staticmethod
    def rows(ctx, p):
        lines = sorted((ln for ln in ctx.body_lines(p.number) if not ln.vertical), key=lambda l: (l.y0 + l.y1) / 2)
        rows = []
        for ln in lines:
            cy = (ln.y0 + ln.y1) / 2
            if rows and abs(rows[-1]["cy"] - cy) <= 0.45 * max(ln.height, rows[-1]["h"]):
                rows[-1]["parts"].append(ln)
                rows[-1]["h"] = max(rows[-1]["h"], ln.height)
            else:
                rows.append({"cy": cy, "h": ln.height, "parts": [ln]})
        for row in rows:
            row["parts"].sort(key=lambda l: l.x0)
        return rows

    @staticmethod
    def parse_row(row, width):
        parts = row["parts"]
        last = parts[-1].text.strip()
        title = num = None
        if len(parts) >= 2 and re.fullmatch(r"\d{1,4}|[ivxlcdm]{1,6}", last, re.I) and parts[-1].x0 >= 0.6 * width:
            title, num = " ".join(p.text for p in parts[:-1]), last
        else:
            m = re.match(r"^(.{3,}?)(?:[.\u2026\u00b7\s]{3,})(\d{1,4}|[ivxlcdm]{1,6})$",
                         " ".join(p.text for p in parts), re.I)
            if m:
                title, num = m.group(1), m.group(2)
        if num is None:
            return None
        title = re.sub(r"[.\u2026\u00b7\s]+$", "", title).strip()
        return None if len(title) < 3 else (title, num, parts[0].x0)

    def run(self, ctx):
        r = self.new_result()
        n = len(ctx.pages)
        scan = min(n, max(40, n // 8))
        toc_pages, parsed = [], []
        for p in ctx.pages[:scan]:
            rows = self.rows(ctx, p)
            ok = [(row, self.parse_row(row, p.width)) for row in rows]
            ok = [(row, x) for row, x in ok if x]
            if len(ok) >= 10 and len(ok) / max(1, len(rows)) >= 0.5:
                toc_pages.append(p.number)
                parsed.extend(x for _, x in ok)
        xs = sorted({round(x[2] / 8) for x in parsed})
        rank = {x: i + 1 for i, x in enumerate(xs)}
        entries = []
        for title, num, x0 in parsed:
            val = int(num) if num.isdigit() else roman_to_int(num)
            if val is not None:
                entries.append((rank[round(x0 / 8)], title, val, "arabic" if num.isdigit() else "roman"))
        offset = ctx.shared.get("dominant_offset")
        verified = checked = 0
        for lvl, title, val, kind in entries[:80]:
            if kind != "arabic" or offset is None:
                continue
            pno = val + offset
            if 1 <= pno <= n:
                checked += 1
                key = alnum_key(title)[:24]
                page_key = alnum_key(" ".join(ln.text for ln in ctx.page(pno).lines))
                verified += 1 if key and key in page_key else 0
        ctx.shared["printed_toc_entries"] = [(l, t, v) for l, t, v, k in entries if k == "arabic"]
        r.facts = {"toc_pages": toc_pages, "entries_parsed": len(entries),
                   "verified_on_target_page": f"{verified}/{checked}", "sample": [e[1] for e in entries[:8]]}
        if toc_pages:
            r.say(f"A printed table of contents was found on PDF pages {compress_ranges(toc_pages)} with "
                  f"{len(entries)} entries; {verified}/{checked} sampled entries were found on the page their number points to.")
            r.add_table("Sample printed-TOC entries", ["Level (by indent)", "Title", "Printed page"],
                        [[e[0], clip(e[1], 70), e[2]] for e in entries[:12]])
        else:
            r.say("No printed table of contents detected in the first pages.")
        r.score("Printed TOC", "info", f"{len(entries)} entries on {len(toc_pages)} pages" if toc_pages else "not found")
        r.recipe = {"printed_toc_pages": toc_pages, "entries": len(entries)}
        for pg in toc_pages[:1]:
            ctx.add_evidence("printed_toc_page", pg)
        return r


# ======================================================================
# Analyzer: outline (bookmarks) -> section tree + classification
# ======================================================================
@register
class OutlineAnalyzer(Analyzer):
    key = "outline"
    title = "Outline and section structure"
    requires = ("printed_toc",)

    def run(self, ctx):
        r = self.new_result()
        n = len(ctx.pages)
        raw = ctx.doc.get_toc()
        entries, unresolved = [], 0
        for lvl, title, page in raw:
            if page is None or page < 1:
                unresolved += 1
                page = 0
            entries.append(Entry(len(entries), int(lvl), norm_ws(title), int(page)))
        source = "outline"
        if len(entries) < 3:
            ptoc = ctx.shared.get("printed_toc_entries") or []
            if len(ptoc) >= 5:
                off = ctx.shared.get("dominant_offset") or 0
                entries = [Entry(i, int(l), t, int(v + off), "printed_toc") for i, (l, t, v) in enumerate(ptoc)]
                source = "printed_toc"
                r.warn("The PDF has no usable bookmarks; the section tree was built from the printed table of contents.")
            else:
                r.problem("The PDF has no bookmarks and no parsable printed table of contents. Sections will have to be "
                          "derived from typography (see 'Typography' and 'Heading inventory').")
        stack = []
        for e in entries:
            while stack and entries[stack[-1]].level >= e.level:
                stack.pop()
            e.parent = stack[-1] if stack else None
            stack.append(e.index)
        for i, e in enumerate(entries):
            nxt = next((x.page for x in entries[i + 1:] if x.level <= e.level and x.page > 0), None)
            e.next_start_page = nxt if nxt else ctx.total_pages
            e.kind, e.use = ctx.classifier.classify(e.title, e.level)
        # children of reference/index/preface-like containers inherit the container's role
        # (e.g. numbered sub-entries under 'References' are reference lists, not teaching sections)
        inherit = {"references", "index", "preface", "contents", "appendix", "answer_key", "glossary"}
        for e in entries:
            if e.parent is not None and entries[e.parent].kind in inherit and e.kind in ("section", "numbered_section"):
                e.kind, e.use = entries[e.parent].kind + "_item", entries[e.parent].use
        ctx.entries = entries
        ctx.shared["entries_source"] = source

        levels = Counter(e.level for e in entries)
        kinds = Counter(e.kind for e in entries)
        children = defaultdict(list)
        for e in entries:
            if e.parent is not None:
                children[e.parent].append(e)
        inversions = [(entries[i - 1], entries[i]) for i in range(1, len(entries))
                      if entries[i].page and entries[i - 1].page and entries[i].page < entries[i - 1].page]
        outside = [(entries[e.parent], e) for e in entries if e.parent is not None and e.page
                   and not (entries[e.parent].page <= e.page <= max(entries[e.parent].next_start_page, entries[e.parent].page))]
        beyond = [e for e in entries if e.page > ctx.total_pages]
        dupes = Counter((e.parent, e.title) for e in entries)
        dup_n = sum(1 for k, c in dupes.items() if c > 1)
        schemes = Counter()
        for e in entries:
            t = e.title
            schemes["'Chapter N ...'" if re.match(r"^(chapter|unit|part)\s+\d+", t, re.I)
                    else "'N.M ...' (decimal)" if re.match(r"^\d+(\.\d+)+\s", t)
                    else "'N ...' (plain number)" if re.match(r"^\d+\s+\S", t) else "unnumbered"] += 1

        top = [e for e in entries if e.level == 1]
        chapters = [e for e in top if len(children.get(e.index, [])) >= 3]
        chapter_kinds = []
        for ch in chapters:
            chapter_kinds.append((ch, Counter(c.kind for c in children[ch.index])))
        presence = Counter()
        for _, kc in chapter_kinds:
            for k in kc:
                presence[k] += 1
        expected = {k for k, c in presence.items() if chapter_kinds and c >= 0.6 * len(chapter_kinds)}
        deviations = []
        for ch, kc in chapter_kinds:
            missing = sorted(expected - set(kc))
            if missing:
                deviations.append((ch.title, missing))
        deepest = max(levels) if levels else 0

        r.facts = {
            "source": source, "entries": len(entries), "levels": dict(levels), "max_depth": deepest,
            "kinds": dict(kinds), "unresolved_destinations": unresolved, "page_inversions": len(inversions),
            "children_outside_parent_range": len(outside), "beyond_last_page": len(beyond),
            "duplicate_titles": dup_n, "numbering_schemes": dict(schemes), "chapter_count": len(chapters),
            "chapter_template_kinds": sorted(expected),
            "chapter_template_deviations": [{"chapter": t, "missing": m} for t, m in deviations],
            "entries_list": [{"index": e.index, "level": e.level, "title": e.title, "page": e.page,
                              "kind": e.kind, "use": e.use, "parent": e.parent,
                              "next_start_page": e.next_start_page} for e in entries],
        }
        if entries:
            r.say(f"{len(entries)} {source} entries, {deepest} level(s) deep ({dict(levels)}). "
                  f"{len(chapters)} chapter-like containers; numbering schemes: {dict(schemes)}.")
            if deepest <= 2:
                r.info("The outline stops at section level; sub-section headings inside sections are NOT in the outline and "
                       "must be found from typography (see 'Heading inventory').")
        if unresolved:
            r.warn(f"{unresolved} outline entries have no resolvable target page.")
        if inversions:
            r.warn(f"{len(inversions)} entries point to an earlier page than the entry before them; e.g. "
                   + "; ".join(f"'{clip(b.title, 30)}' (p{b.page}) after '{clip(a.title, 30)}' (p{a.page})" for a, b in inversions[:3]))
        if outside:
            r.warn(f"{len(outside)} entries lie outside their parent's page range; e.g. "
                   + "; ".join(f"'{clip(c.title, 30)}' (p{c.page}) under '{clip(pa.title, 30)}'" for pa, c in outside[:3]))
        if beyond and n >= ctx.total_pages:
            r.problem(f"{len(beyond)} entries point beyond the last page.")
        if dup_n:
            r.info(f"{dup_n} duplicate titles under the same parent.")

        r.add_table("Level-1 entries", ["#", "Title", "PDF page", "Children", "Kind", "RAG use"],
                    [[e.index, clip(e.title, 60), e.page, len(children.get(e.index, [])), e.kind, e.use]
                     for e in top[:80]], note="Full tree is in profile.json (entries_list).")
        rows = []
        for k, c in kinds.most_common():
            es = [e for e in entries if e.kind == k]
            rows.append([k, es[0].use, c, "; ".join(clip(e.title, 28) for e in es[:2])])
        r.add_table("Section kinds (classification of outline titles)", ["Kind", "RAG use", "Entries", "Examples"], rows,
                    note="Kinds come from the configurable SectionRules; unknown titles at level>=2 default to 'section'.")
        if deviations:
            r.add_table("Chapters that deviate from the common chapter template",
                        ["Chapter", "Missing sections"], [[clip(t, 60), ", ".join(m)] for t, m in deviations[:30]])
            r.info(f"Common chapter template: {', '.join(sorted(expected))}. "
                   f"{len(deviations)} chapters lack part of it.")
        for e in entries:
            if e.page and e.kind not in ("section", "numbered_section"):
                ctx.add_evidence(f"first_{e.kind}", e.page, keep=1)
        if source == "outline" and len(entries) >= 5 and not outside:
            r.score("Outline", "ok", f"{len(entries)} entries, depth {deepest}, consistent")
        elif entries:
            r.score("Outline", "warn", f"from {source}; {len(inversions) + len(outside)} inconsistencies")
        else:
            r.score("Outline", "fail", "no outline and no printed TOC")
        r.recipe = {"source": source, "depth": deepest, "kinds": dict(kinds),
                    "teaching_kinds": sorted({e.kind for e in entries if e.use == "teaching"}),
                    "skip_kinds": sorted({e.kind for e in entries if e.use in ("skip", "metadata")}),
                    "chapter_template": sorted(expected)}
        r.note(f"Section tree from {source}: {len(entries)} entries, {deepest} levels. "
               f"Skip/tag kinds: {', '.join(r.recipe['skip_kinds']) or 'none'}.")
        return r


# ======================================================================
# Analyzer: where do outline entries start on the page?
# ======================================================================
@dataclass
class Located:
    entry: int
    status: str          # exact | nearby | missing
    page: int
    y: float
    x: float
    score: float
    style: int
    size: float
    text: str


@register
class HeadingLocatorAnalyzer(Analyzer):
    key = "heading_locator"
    title = "Heading positions (outline entries found on the page)"
    requires = ("outline", "header_footer")

    @staticmethod
    def candidates(ctx, pno):
        lines = [ln for ln in ctx.body_lines(pno) if not ln.vertical]
        lines.sort(key=lambda l: (round(l.y0, 1), l.x0))
        out = []
        for i, ln in enumerate(lines):
            out.append((ln.text, ln, ln))
            text, last = ln.text, ln
            for k in (1, 2):
                if i + k >= len(lines):
                    break
                nxt = lines[i + k]
                gap = nxt.y0 - last.y1
                if gap > 1.2 * max(nxt.height, 8) or gap < -2 or abs(nxt.x0 - ln.x0) > 150:
                    break
                text = text + " " + nxt.text
                last = nxt
                out.append((text, ln, nxt))
        return out

    @staticmethod
    def match(target, cands, ctx, thr):
        best = None
        for text, first, last in cands:
            key = alnum_key(text)
            if not key:
                continue
            if key == target:
                score = 1.0
            elif abs(len(key) - len(target)) <= 0.35 * max(len(target), 1):
                if key.startswith(target) or target.startswith(key):
                    score = 0.95 if min(len(key), len(target)) >= 0.6 * max(len(key), len(target)) else 0.0
                else:
                    score = difflib.SequenceMatcher(None, key, target).ratio()
            else:
                continue
            if score >= thr:
                size = ctx.styles.get(first.dom).size
                if best is None or (score, size) > (best[0], best[1]):
                    best = (score, size, first, last)
        return best

    def run(self, ctx):
        r = self.new_result()
        cfg = ctx.config
        n = len(ctx.pages)
        entries = [e for e in ctx.entries if 1 <= e.page <= n]
        cache = {}

        def cands(pno):
            if pno not in cache:
                cache[pno] = self.candidates(ctx, pno)
            return cache[pno]

        located: dict[int, Located] = {}
        for e in entries:
            target = alnum_key(e.title)
            hit = None
            for pno, status in ((e.page, "exact"), (e.page + 1, "nearby"), (e.page - 1, "nearby")):
                if not 1 <= pno <= n:
                    continue
                m = self.match(target, cands(pno), ctx, cfg.heading_match_ratio)
                if m:
                    hit = (pno, status, m)
                    break
            if hit:
                pno, status, (score, size, first, _last) = hit
                located[e.index] = Located(e.index, status, pno, first.y0, first.x0, round(score, 3),
                                           first.dom, size, first.text)
            else:
                located[e.index] = Located(e.index, "missing", e.page, 0.0, 0.0, 0.0, -1, 0.0, "")
        ctx.shared["located"] = located

        found = [l for l in located.values() if l.status != "missing"]
        exact = [l for l in located.values() if l.status == "exact"]
        missing = [l for l in located.values() if l.status == "missing"]
        by_kind = defaultdict(lambda: [0, 0])
        for l in located.values():
            k = ctx.entries[l.entry].kind
            by_kind[k][1] += 1
            by_kind[k][0] += l.status != "missing"

        top_y = {}
        mid = non_mid = 0
        for l in exact:
            e = ctx.entries[l.entry]
            if e.kind in ("chapter", "index", "contents", "preface", "references"):
                continue
            if l.page not in top_y:
                ys = [ln.y0 for ln in ctx.body_lines(l.page)]
                top_y[l.page] = min(ys) if ys else 0.0
            if l.y - top_y[l.page] > cfg.mid_page_threshold_pt:
                mid += 1
                ctx.add_evidence("section_starts_mid_page", l.page)
            else:
                non_mid += 1

        learned = defaultdict(Counter)
        for l in exact:
            learned[l.style][ctx.entries[l.entry].level] += 1
        ctx.shared["heading_styles"] = {sid: dict(c) for sid, c in learned.items()}

        # ---- which entry "owns" each page (dominant by vertical extent) --------
        cuts_by_page = defaultdict(list)
        for l in located.values():
            if 1 <= l.page <= n:
                cuts_by_page[l.page].append((l.y if l.status != "missing" else 0.0, l.entry))
        page_entry, current = {}, None
        for p in ctx.pages:
            cuts = sorted(cuts_by_page.get(p.number, []))
            body = ctx.body_lines(p.number)
            top = min((ln.y0 for ln in body), default=0.0)
            bottom = max((ln.y1 for ln in body), default=p.height)
            segs, prev, start = [], current, top
            for y, idx in cuts:
                if prev is not None:
                    segs.append((prev, start, max(start, y)))
                prev, start = idx, max(y, top)
            if prev is not None:
                segs.append((prev, start, max(start, bottom)))
            page_entry[p.number] = max(segs, key=lambda s: s[2] - s[1])[0] if segs else None
            current = prev
        ctx.shared["page_entry"] = {k: v for k, v in page_entry.items() if v is not None}
        ctx.shared["cuts"] = sorted((l.page, l.y, l.entry) for l in located.values() if 1 <= l.page <= n)

        r.facts = {
            "entries_checked": len(located), "found_on_target_page": len(exact),
            "found_on_adjacent_page": len(found) - len(exact), "not_found": len(missing),
            "sections_start_mid_page": mid, "sections_start_at_page_top": non_mid,
            "found_by_kind": {k: f"{a}/{b}" for k, (a, b) in by_kind.items()},
            "heading_styles_by_outline_level": {ctx.styles.get(sid).label(): dict(c) for sid, c in learned.items()},
            "missing": [{"title": ctx.entries[l.entry].title, "page": l.page} for l in missing[:100]],
            "located": [vars(l) for l in located.values()],
        }
        r.say(f"{len(found)}/{len(located)} outline titles were found as text on their page "
              f"({len(exact)} exact, {len(found) - len(exact)} on an adjacent page); {len(missing)} not found.")
        if mid + non_mid:
            r.say(f"{pct(mid, mid + non_mid)} of section headings start MID-page "
                  f"(more than {cfg.mid_page_threshold_pt:g}pt below the first body line). "
                  + ("Therefore sections must be cut at the heading position (page, y), not at page boundaries."
                     if mid > 0.2 * (mid + non_mid) else "Most sections start at the top of a page."))
        if learned:
            r.add_table("Heading styles learned from the outline (ground truth)",
                        ["Outline level", "Style", "Occurrences", "Share of that level"],
                        [[lvl, ctx.styles.get(sid).label(), cnt,
                          pct(cnt, sum(c.get(lvl, 0) for c in learned.values()))]
                         for sid, c in learned.items() for lvl, cnt in sorted(c.items())])
        r.add_table("Locating rate by section kind", ["Kind", "Found / total"],
                    [[k, f"{a}/{b}"] for k, (a, b) in sorted(by_kind.items())])
        if missing:
            r.add_table("Outline titles NOT found on their page", ["Page", "Level", "Title"],
                        [[l.page, ctx.entries[l.entry].level, clip(ctx.entries[l.entry].title, 70)] for l in missing[:25]],
                        note="Likely causes: title text differs from the printed heading, heading is an image, or outline page is wrong.")
            ctx.add_evidence("outline_title_not_found", missing[0].page)
        rate = ratio(len(found), len(located))
        if not located:
            r.score("Heading positions", "fail", "no outline entries to locate")
        elif rate >= 0.95:
            r.score("Heading positions", "ok", f"{pct(len(found), len(located))} of outline titles located")
        elif rate >= 0.7:
            r.score("Heading positions", "warn", f"{pct(len(found), len(located))} located; review the missing list")
        else:
            r.score("Heading positions", "fail", f"only {pct(len(found), len(located))} located")
        r.recipe = {"strategy": "match outline titles to page lines (fuzzy, multi-line aware)",
                    "found_rate": round(rate, 3), "cut_sections_at_heading_y": mid > 0.2 * max(1, mid + non_mid),
                    "heading_styles_by_outline_level": r.facts["heading_styles_by_outline_level"]}
        r.note("Cut sections at the heading's (page, y); lines above it on the same page belong to the previous section."
               if r.recipe["cut_sections_at_heading_y"] else "Sections mostly start at page tops.")
        return r


# ======================================================================
# Analyzer: which pages are not numbered and what are they?
# ======================================================================
@register
class PageCoverageAnalyzer(Analyzer):
    key = "page_coverage"
    title = "Page coverage by section kind"
    requires = ("heading_locator", "page_numbering")

    def run(self, ctx):
        r = self.new_result()
        pe = ctx.shared.get("page_entry", {})
        unnumbered = set(ctx.shared.get("unnumbered_pages", []))
        per_kind, per_kind_unnum = Counter(), Counter()
        openers = 0
        for p in ctx.pages:
            idx = pe.get(p.number)
            kind = ctx.entries[idx].kind if idx is not None else "(before first entry)"
            per_kind[kind] += 1
            if p.number in unnumbered:
                per_kind_unnum[kind] += 1
        r.facts = {"pages_by_kind": dict(per_kind), "unnumbered_by_kind": dict(per_kind_unnum)}
        r.add_table("Pages by section kind", ["Kind", "Pages", "Pages without printed number"],
                    [[k, c, per_kind_unnum.get(k, 0)] for k, c in per_kind.most_common()],
                    note="A page is assigned to the section that covers most of its height.")
        if unnumbered:
            top = per_kind_unnum.most_common(4)
            r.say("Pages without a printed number belong mostly to: "
                  + ", ".join(f"{k} ({c})" for k, c in top) + ".")
        r.recipe = {"pages_by_kind": dict(per_kind)}
        return r



# ======================================================================
# Analyzer: typography (style inventory + role inference)
# ======================================================================
@dataclass
class StyleStat:
    chars: int = 0
    dom_lines: int = 0
    pure_lines: int = 0
    pure_chars: int = 0
    band_lines: int = 0
    first_lines: int = 0
    caption_hits: int = 0
    caps_pure: int = 0
    words_pure: int = 0
    examples: list = field(default_factory=list)
    pages: set = field(default_factory=set)


@register
class TypographyAnalyzer(Analyzer):
    key = "typography"
    title = "Typography: styles and their roles"
    requires = ("heading_locator",)

    def run(self, ctx):
        r = self.new_result()
        cfg = ctx.config
        stats: dict[int, StyleStat] = defaultdict(StyleStat)
        for p in ctx.pages:
            for ln in p.lines:
                band = ctx.band_of(ln, p)
                for sp in ln.spans:
                    stats[sp.style].chars += len(sp.text.strip())
                st = stats[ln.dom]
                st.dom_lines += 1
                st.pages.add(p.number)
                if band:
                    st.band_lines += 1
                first = stats[ln.spans[0].style]
                first.first_lines += 1
                if ctx.caption_re.match(ln.text):
                    first.caption_hits += 1
                if ln.pure:
                    st.pure_lines += 1
                    st.pure_chars += len(ln.text)
                    st.words_pure += len(ln.text.split())
                    letters = [c for c in ln.text if c.isalpha()]
                    if len(letters) >= 3 and ln.text.isupper():
                        st.caps_pure += 1
                    ex = clip(ln.text, 45)
                    if not band and len(ex) >= 3 and ex not in st.examples and len(st.examples) < cfg.examples_per_item:
                        st.examples.append(ex)

        body = ctx.body_style_id
        bs = ctx.styles.get(body)
        total_chars = sum(s.chars for s in stats.values()) or 1
        confirmed = {}
        for sid, c in ctx.shared.get("heading_styles", {}).items():
            confirmed[sid] = max(c.items(), key=lambda kv: kv[1])[0]

        roles = {}
        for sid, st in stats.items():
            roles[sid] = self._infer(ctx, sid, st, bs, body, confirmed)

        # proposed heading levels
        heading_sids = [s for s, (code, *_x) in roles.items() if code in ("heading_confirmed", "heading_candidate")]
        proposed = {}
        if confirmed:
            max_level = max(confirmed.values())
            min_conf_size = min(ctx.styles.get(s).size for s in confirmed)
            max_conf_size = max(ctx.styles.get(s).size for s in confirmed)
            smaller = sorted({(ctx.styles.get(s).size, ctx.styles.get(s).bold) for s in heading_sids
                              if s not in confirmed and ctx.styles.get(s).size <= min_conf_size + 0.05},
                             key=lambda t: (-t[0], not t[1]))
            lvl_of = {k: max_level + i + 1 for i, k in enumerate(smaller)}
            for s in heading_sids:
                st_ = ctx.styles.get(s)
                if s in confirmed:
                    proposed[s] = f"{confirmed[s]} (outline-confirmed)"
                elif st_.size > max_conf_size + 0.05:
                    proposed[s] = "above outline levels (title / opener)"
                else:
                    proposed[s] = f"{lvl_of.get((st_.size, st_.bold), max_level + 1)} (inferred)"
        else:
            order = sorted({(ctx.styles.get(s).size, ctx.styles.get(s).bold) for s in heading_sids},
                           key=lambda t: (-t[0], not t[1]))
            lvl_of = {k: i + 1 for i, k in enumerate(order)}
            for s in heading_sids:
                st_ = ctx.styles.get(s)
                proposed[s] = f"{lvl_of[(st_.size, st_.bold)]} (inferred, no outline)"
        ctx.shared["style_roles"] = {sid: roles[sid][0] for sid in roles}
        ctx.shared["style_levels"] = proposed

        order = sorted(stats, key=lambda s: -stats[s].chars)
        shown = list(order[:30])
        for s in order[30:]:
            if roles[s][0] in ("heading_confirmed", "heading_candidate", "label_caps", "caption_label") and len(shown) < 50:
                shown.append(s)
        rows = []
        for s in shown:
            st = stats[s]
            code, text, conf = roles[s]
            rows.append([ctx.styles.get(s).label(), f"{st.chars:,}", pct(st.chars, total_chars),
                         st.pure_lines, len(st.pages), f"{text} ({conf})", proposed.get(s, ""),
                         "; ".join(st.examples)])
        r.add_table("Text styles (sorted by amount of text; role is inferred unless marked outline-confirmed)",
                    ["Style (font size flags colour)", "Chars", "Share", "Standalone lines", "Pages", "Role (confidence)",
                     "Heading level", "Examples"], rows, note="'Standalone lines' = lines made entirely of this style.")

        by_code = defaultdict(list)
        for s in order:
            by_code[roles[s][0]].append(ctx.styles.get(s).label())
        r.facts = {
            "body_style": bs.label(), "styles": [{
                "style": ctx.styles.get(s).label(), "font": ctx.styles.get(s).font, "size": ctx.styles.get(s).size,
                "bold": ctx.styles.get(s).bold, "italic": ctx.styles.get(s).italic, "color": ctx.styles.get(s).color,
                "chars": stats[s].chars, "share": round(stats[s].chars / total_chars, 5),
                "standalone_lines": stats[s].pure_lines, "pages": len(stats[s].pages),
                "role": roles[s][0], "role_text": roles[s][1], "confidence": roles[s][2],
                "heading_level": proposed.get(s), "examples": stats[s].examples} for s in order],
        }
        r.say(f"Body text style: {bs.label()} ({pct(stats[body].chars, total_chars)} of all characters). "
              f"{len(ctx.styles)} styles in total; {len(confirmed)} confirmed as headings by the outline, "
              f"{sum(1 for s in heading_sids if s not in confirmed)} more look like headings.")
        r.say("Confidence 'high' = confirmed by the outline or by position; 'medium' = rule-based guess; "
              "'low' = unclear. Review the examples before trusting a role.")
        r.score("Typography", "ok" if confirmed else "warn",
                f"body style identified; {len(confirmed)} heading style(s) confirmed by outline"
                if confirmed else "no outline-confirmed heading styles; roles are guesses")
        r.recipe = {
            "body_style": bs.label(),
            "heading_styles": [{"style": ctx.styles.get(s).label(), "level": proposed[s]} for s in heading_sids],
            "label_styles": by_code.get("label_caps", []), "caption_label_styles": by_code.get("caption_label", []),
            "inline_emphasis_styles": by_code.get("inline_emphasis", []),
            "coloured_inline_styles": by_code.get("coloured_inline", []),
            "small_text_styles": by_code.get("small_text", []),
        }
        r.note(f"Classify a text line by its DOMINANT style (most characters), not by its first span: superscripts and "
               f"symbols split lines into spans of different sizes.")
        for s in heading_sids[:6]:
            r.note(f"Heading style {ctx.styles.get(s).label()} -> level {proposed[s]}.")
        return r

    @staticmethod
    def _infer(ctx, sid, st, bs, body, confirmed):
        s = ctx.styles.get(sid)
        size_ratio = s.size / bs.size if bs.size else 1.0
        band_share = ratio(st.band_lines, st.dom_lines)
        pure_share = ratio(st.pure_chars, st.chars)
        avg_words = ratio(st.words_pure, st.pure_lines)
        caps_share = ratio(st.caps_pure, st.pure_lines)
        cap_share = ratio(st.caption_hits, st.first_lines)
        if sid == body:
            return "body", "body text", "high"
        if band_share >= 0.7 and st.dom_lines >= 3:
            return "running", "running header/footer/page number", "high"
        if sid in confirmed:
            return "heading_confirmed", f"heading (outline level {confirmed[sid]})", "high"
        if cap_share >= 0.4 and st.first_lines >= 3:
            return "caption_label", "caption / table label", "medium"
        if st.pure_lines >= 3 and pure_share >= 0.6 and avg_words <= 14:
            if caps_share >= 0.7 and avg_words <= 6:
                return "label_caps", "ALL-CAPS label (callout/box label or structural label)", "medium"
            if size_ratio >= 1.05 or s.bold:
                return "heading_candidate", "heading / title candidate", "medium"
        if size_ratio < 0.92:
            return "small_text", "small text (captions, footnotes, super/subscripts)", "medium"
        if abs(size_ratio - 1) <= 0.06:
            if (s.bold or s.italic) and pure_share < 0.5:
                return "inline_emphasis", "inline emphasis (bold/italic terms)", "medium"
            if s.color != bs.color and pure_share < 0.5:
                return "coloured_inline", "coloured inline text (links / cross-references)", "medium"
            if pure_share >= 0.5:
                return "body_variant", "body variant (list, question or objective text)", "low"
        return "other", "other / unclear", "low"


# ======================================================================
# Analyzer: heading inventory (headings that are NOT in the outline)
# ======================================================================
@register
class HeadingInventoryAnalyzer(Analyzer):
    key = "heading_inventory"
    title = "Heading inventory (including sub-headings missing from the outline)"
    requires = ("typography",)

    def run(self, ctx):
        r = self.new_result()
        roles = ctx.shared.get("style_roles", {})
        levels = ctx.shared.get("style_levels", {})
        located = ctx.shared.get("located", {})
        matched = {(l.page, round(l.y, 1)) for l in located.values() if l.status != "missing"}
        wanted = {sid for sid, code in roles.items() if code in ("heading_confirmed", "heading_candidate", "label_caps")}
        counts = defaultdict(lambda: [0, 0, []])      # sid -> [standalone, in_outline, examples not in outline]
        by_chapter = defaultdict(Counter)
        for p in ctx.pages:
            for ln in ctx.body_lines(p.number):
                if ln.dom not in wanted or not ln.pure or ln.vertical or len(ln.text.split()) > 18:
                    continue
                c = counts[ln.dom]
                c[0] += 1
                if (p.number, round(ln.y0, 1)) in matched:
                    c[1] += 1
                else:
                    if len(c[2]) < 6:
                        c[2].append(f"{clip(ln.text, 40)} (p{p.number})")
                    by_chapter[ln.dom][ctx.kind_of_page(p.number)] += 1
        rows = []
        for sid, (tot, inol, ex) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
            rows.append([ctx.styles.get(sid).label(), roles[sid], levels.get(sid, ""), tot, inol, tot - inol, "; ".join(ex[:4])])
        r.add_table("Standalone lines per heading-like style",
                    ["Style", "Role", "Level", "Standalone lines", "Matched to outline", "Not in outline", "Examples not in outline"],
                    rows, note="'Not in outline' on a heading style usually means sub-headings (or a repeated label).")
        sub = [(sid, tot - inol) for sid, (tot, inol, _ex) in counts.items()
               if roles.get(sid) == "heading_candidate" and tot - inol >= 5]
        r.facts = {"styles": {ctx.styles.get(sid).label(): {"standalone": t, "in_outline": i, "not_in_outline": t - i,
                                                             "examples": ex} for sid, (t, i, ex) in counts.items()}}
        if sub:
            total_sub = sum(n for _, n in sub)
            teaching = sum(1 for e in ctx.entries if e.use == "teaching" and e.level >= 2) or 1
            r.say(f"{total_sub} heading-like lines are not in the outline (probable sub-headings), about "
                  f"{total_sub / teaching:.1f} per teaching section. Styles: "
                  + ", ".join(f"{ctx.styles.get(sid).label()} ({n})" for sid, n in sub) + ".")
            r.recipe = {"subheading_styles": [{"style": ctx.styles.get(sid).label(), "count": n,
                                               "level": levels.get(sid, "")} for sid, n in sub]}
            r.note("Detect sub-headings as standalone lines in: " + ", ".join(ctx.styles.get(sid).label() for sid, _ in sub)
                   + ". Attach them to the section in which they occur.")
            r.score("Sub-headings", "ok", f"{total_sub} sub-heading lines found by typography")
        else:
            r.say("No additional heading styles found beyond the outline entries.")
            r.score("Sub-headings", "info", "none found beyond the outline")
        return r


# ======================================================================
# Analyzer: page layout (columns, margins, indentation)
# ======================================================================
@register
class LayoutAnalyzer(Analyzer):
    key = "layout"
    title = "Page layout: columns, margins, indentation"
    requires = ("heading_locator",)

    @staticmethod
    def detect_columns(ctx, p, body):
        lines = [l for l in body if not l.vertical and len(l.text) >= 2]
        cfg = ctx.config
        if len(lines) < 2 * cfg.column_min_lines_per_side:
            return 1, None
        W = p.width
        best, best_x = None, []
        for x in range(int(0.35 * W), int(0.65 * W) + 1, 2):
            cross = sum(1 for l in lines if l.x0 < x - 1 and l.x1 > x + 1)
            if best is None or cross < best:
                best, best_x = cross, [x]
            elif cross == best:
                best_x.append(x)
        if best is None or best > max(1, cfg.column_max_crossing * len(lines)):
            return 1, None
        gx = min(best_x, key=lambda x: abs(x - W / 2))
        left = [l for l in lines if l.x1 <= gx + 2]
        right = [l for l in lines if l.x0 >= gx - 2]
        if len(left) < cfg.column_min_lines_per_side or len(right) < cfg.column_min_lines_per_side:
            return 1, None
        ly0, ly1 = min(l.y0 for l in left), max(l.y1 for l in left)
        ry0, ry1 = min(l.y0 for l in right), max(l.y1 for l in right)
        overlap = max(0.0, min(ly1, ry1) - max(ly0, ry0))
        if overlap < 0.5 * min(ly1 - ly0, ry1 - ry0):
            return 1, None
        return 2, gx

    def run(self, ctx):
        r = self.new_result()
        body_sid = ctx.body_style_id
        page_cols, gutters = {}, []
        for p in ctx.pages:
            if p.chars < 20:
                continue
            ncol, gx = self.detect_columns(ctx, p, ctx.body_lines(p.number))
            page_cols[p.number] = (ncol, gx)
            if ncol == 2:
                gutters.append(gx)
        ctx.shared["page_columns"] = page_cols
        two = [pn for pn, (c, _g) in page_cols.items() if c == 2]

        kind_tot, kind_two = Counter(), Counter()
        for pn in page_cols:
            k = ctx.kind_of_page(pn)
            kind_tot[k] += 1
            kind_two[k] += pn in set(two)
        table_pages = {pn for pn, _l, kind, _lab in ctx.captions if kind == "table"}
        teaching_two = [pn for pn in two if ctx.kind_of_page(pn) in ("section", "numbered_section", "introduction")]
        teaching_two_tables = [pn for pn in teaching_two if pn in table_pages]

        x0s, left_even, left_odd, rights, tops, bottoms = Counter(), [], [], [], [], []
        for p in ctx.pages:
            if page_cols.get(p.number, (1, None))[0] != 1:
                continue
            body = [l for l in ctx.body_lines(p.number) if not l.vertical]
            long_ = [l for l in body if l.dom == body_sid and len(l.text) >= 40]
            if len(long_) < 5:
                continue
            for l in body:
                x0s[round(l.x0)] += 1
            mn = Counter(round(l.x0) for l in long_).most_common(1)[0][0]
            (left_odd if p.number % 2 else left_even).append(mn)
            rights.append(percentile([l.x1 for l in long_], 0.95))
            tops.append(min(l.y0 for l in body))
            bottoms.append(max(l.y1 for l in body))
        left = median(left_odd + left_even)
        right = median(rights)
        layout = {"text_left": left, "text_right": right, "text_width": (right - left) if left and right else None,
                  "body_top": median(tops), "body_bottom": median(bottoms)}
        ctx.shared["layout"] = layout

        r.facts = {"two_column_pages": len(two), "two_column_ranges": compress_ranges(two, 200),
                   "median_gutter_x": median(gutters), "by_kind": {k: f"{kind_two[k]}/{kind_tot[k]}" for k in kind_tot},
                   "teaching_pages_two_column": len(teaching_two),
                   "teaching_two_column_with_table_caption": len(teaching_two_tables),
                   "margins": {k: (round(v, 1) if v is not None else None) for k, v in layout.items()},
                   "left_margin_odd": median(left_odd), "left_margin_even": median(left_even),
                   "line_start_positions": dict(x0s.most_common(12))}
        if layout["text_left"] is not None:
            r.say(f"Single-column text block: x from {left:.0f}pt to {right:.0f}pt (width {right - left:.0f}pt), "
                  f"vertical extent about y {layout['body_top']:.0f}-{layout['body_bottom']:.0f}pt.")
        if two:
            r.say(f"{len(two)} pages ({pct(len(two), len(page_cols))}) are two-column (gutter near x={median(gutters):.0f}pt). "
                  "Read the left column top-to-bottom, then the right column.")
            r.add_table("Two-column pages by section kind", ["Kind", "Pages", "Two-column pages", "Share"],
                        [[k, kind_tot[k], kind_two[k], pct(kind_two[k], kind_tot[k], 0)]
                         for k in sorted(kind_tot, key=lambda k: -kind_two[k]) if kind_two[k]])
            if teaching_two:
                r.info(f"{len(teaching_two)} two-column pages fall inside teaching sections "
                       f"({len(teaching_two_tables)} of them carry a table caption, i.e. probably wide tables, not text columns).")
            for pn in two[:1]:
                ctx.add_evidence("two_column_page", pn)
            for k in sorted(kind_two, key=lambda k: -kind_two[k])[:2]:
                if kind_two[k]:
                    pn = next(p for p in two if ctx.kind_of_page(p) == k)
                    ctx.add_evidence(f"two_column_{k}", pn, keep=1)
        else:
            r.say("No two-column pages detected.")
        r.add_table("Most common line-start x positions (single-column pages)", ["x (pt)", "Lines"],
                    [[x, c] for x, c in x0s.most_common(10)],
                    note="Several distinct x values reveal indentation levels (lists, boxes, hanging indents).")
        r.score("Columns", "info", f"{len(two)} two-column pages" if two else "single column throughout")
        r.recipe = {"two_column_pages": len(two), "gutter_x_pt": median(gutters),
                    "two_column_kinds": sorted(k for k in kind_two if kind_two[k] >= 0.5 * kind_tot[k]),
                    "reading_order": "column-major (left column, then right column)" if two else "top-to-bottom",
                    "text_block": {k: (round(v, 1) if v else v) for k, v in layout.items()}}
        r.note("Order lines by (column, y). Detect columns per page: >= "
               f"{ctx.config.column_min_lines_per_side} lines on each side of a vertical gutter."
               if two else "Order lines by y.")
        return r


# ======================================================================
# Analyzer: block structure (paragraphs, lists, hyphenation, sub/superscripts)
# ======================================================================
BULLET_RE = re.compile(r"^\s*([\u2022\u25E6\u25AA\u25CF\u25A0\u2013\u2014\u00B7*\u25BA\u2023\u2043])\s*\S")
NUMBERED_RE = re.compile(r"^\s*(\d{1,3})\s*[.)]\s+\S")
LETTERED_RE = re.compile(r"^\s*\(?([a-z])[.)]\s+\S")


@register
class BlockStructureAnalyzer(Analyzer):
    key = "block_structure"
    title = "Block structure: paragraphs, lists, hyphenation, symbols"
    requires = ("layout",)

    def run(self, ctx):
        r = self.new_result()
        body_sid = ctx.body_style_id
        bsize = ctx.body_style.size
        cols = ctx.shared.get("page_columns", {})
        pitch_c, indent_c = Counter(), Counter()
        gaps = []
        hy_total = hy_soft = 0
        hy_examples = []
        soft_u00ad = 0
        bullets, numbered, lettered = Counter(), 0, 0
        subsup = body_lines_n = split_pairs = 0
        foot_pages = set()
        long_lines = 0
        for p in ctx.pages:
            if p.chars < 20:
                continue
            ncol = cols.get(p.number, (1, None))[0]
            lines = sorted((l for l in ctx.body_lines(p.number) if not l.vertical), key=lambda l: (l.y0, l.x0))
            if ncol != 1:
                continue
            left = Counter(round(l.x0) for l in lines if l.dom == body_sid).most_common(1)
            left = left[0][0] if left else None
            for i, ln in enumerate(lines):
                body_lines_n += 1
                soft_u00ad += ln.text.count("\u00ad")
                if BULLET_RE.match(ln.text):
                    bullets[BULLET_RE.match(ln.text).group(1)] += 1
                if NUMBERED_RE.match(ln.text):
                    numbered += 1
                elif LETTERED_RE.match(ln.text):
                    lettered += 1
                if len(ln.spans) > 1:
                    dsz = ctx.styles.get(ln.dom).size
                    if any(ctx.styles.get(s.style).size < 0.8 * dsz for s in ln.spans):
                        subsup += 1
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    dy = nxt.y0 - ln.y0
                    if ln.dom == body_sid and nxt.dom == body_sid and abs(nxt.x0 - ln.x0) < 25 and 0 < dy < 3 * bsize:
                        pitch_c[round(dy, 1)] += 1
                    cy1, cy2 = (ln.y0 + ln.y1) / 2, (nxt.y0 + nxt.y1) / 2
                    if abs(cy1 - cy2) <= 0.4 * max(ln.height, nxt.height) and 0 <= nxt.x0 - ln.x1 <= 30:
                        split_pairs += 1
                    if len(ln.text) >= 30 and re.search(r"[A-Za-z]-$", ln.text):
                        hy_total += 1
                        if nxt.text[:1].islower():
                            hy_soft += 1
                        if len(hy_examples) < 6:
                            hy_examples.append(f"...{ln.text[-22:]} | {nxt.text[:22]}...")
                if len(ln.text) >= 30:
                    long_lines += 1
            # paragraph gaps and first-line indents
            prev = None
            for ln in lines:
                if prev is not None and ln.dom == body_sid and prev.dom == body_sid:
                    dy = ln.y0 - prev.y0
                    if dy > 0:
                        gaps.append(dy)
                        if left is not None and dy > 1.25 * (pitch_c.most_common(1)[0][0] if pitch_c else bsize * 1.3):
                            indent_c[round(ln.x0 - left)] += 1
                prev = ln
            lay = ctx.shared.get("layout", {})
            bt, bb = lay.get("body_top"), lay.get("body_bottom")
            if bt is not None and bb is not None:
                limit = bb - 0.2 * (bb - bt)
                if any(l.y0 > limit and ctx.styles.get(l.dom).size < 0.9 * bsize and re.match(r"^[\d*\u2020\u2021\u00a7]", l.text)
                       and len(l.text) >= 20 and not ctx.caption_re.match(l.text) for l in lines):
                    foot_pages.add(p.number)
        pitch = pitch_c.most_common(1)[0][0] if pitch_c else None
        para_gaps = [g for g in gaps if pitch and 1.25 * pitch < g < 3 * pitch]
        indent_main = indent_c.most_common(3)
        r.facts = {
            "line_pitch_pt": pitch, "paragraph_gap_pt_median": median(para_gaps),
            "first_line_indent_pt": dict(indent_main),
            "hyphenated_line_ends": hy_total, "hyphen_followed_by_lowercase": hy_soft,
            "hyphenation_rate": round(ratio(hy_total, long_lines), 4), "hyphen_examples": hy_examples,
            "soft_hyphen_chars": soft_u00ad, "bullet_glyphs": dict(bullets), "numbered_lines": numbered,
            "lettered_lines": lettered, "lines_with_sub_superscripts": subsup,
            "sub_superscript_rate": round(ratio(subsup, body_lines_n), 4),
            "mid_line_fragment_pairs": split_pairs, "footnote_candidate_pages": len(foot_pages),
        }
        if pitch:
            r.say(f"Line pitch is {pitch}pt; a gap larger than ~{pitch * 1.25:.1f}pt between lines of the body style "
                  f"starts a new paragraph. " + (f"First-line indents seen: {dict(indent_main)}." if indent_main
                                                 else "Paragraphs do not use first-line indents."))
        if hy_total:
            r.say(f"{hy_total} body lines end with a hyphen ({pct(hy_total, long_lines)} of long lines); "
                  f"{pct(hy_soft, hy_total, 0)} continue with a lowercase letter. Hyphens can be line-break hyphens "
                  f"(\"pro-\" + \"cess\") or real prefix/compound hyphens (\"non-\" + \"surgical\"), so do not delete them blindly.")
        if subsup:
            r.say(f"{pct(subsup, body_lines_n)} of body lines contain sub/superscript spans (smaller than the line's main "
                  "size). Such spans must not be mistaken for captions or headings.")
        if split_pairs:
            r.say(f"{split_pairs} line pairs sit on the same baseline with a small gap (inline symbols/images split the line); "
                  "merge them into one logical line before classifying.")
        if bullets or numbered or lettered:
            r.say(f"Lists: bullets {dict(bullets)}, numbered lines {numbered}, lettered lines {lettered}.")
        if foot_pages:
            r.info(f"Possible footnotes on {len(foot_pages)} pages (small numbered lines at the bottom).")
        if soft_u00ad:
            r.info(f"{soft_u00ad} soft-hyphen characters (U+00AD) in the text; strip them.")
        r.score("Paragraphs", "ok" if pitch else "warn", f"line pitch {pitch}pt" if pitch else "could not measure line pitch")
        r.recipe = {"line_pitch_pt": pitch, "paragraph_break_gap_pt": round(pitch * 1.25, 1) if pitch else None,
                    "first_line_indent_pt": dict(indent_main),
                    "hyphenation": "keep hyphen, join lines with a space" if hy_total else "n/a",
                    "merge_same_baseline_fragments": bool(split_pairs), "strip_soft_hyphens": bool(soft_u00ad),
                    "bullet_glyphs": list(bullets)}
        r.note(f"New paragraph when the vertical gap exceeds {r.recipe['paragraph_break_gap_pt']}pt "
               "or the first line is indented." if pitch else "Paragraph spacing could not be measured.")
        return r


# ======================================================================
# Analyzer: reading order (is the PDF stream order usable?)
# ======================================================================
@register
class ReadingOrderAnalyzer(Analyzer):
    key = "reading_order"
    title = "Reading order of the raw text stream"
    requires = ("layout",)

    def run(self, ctx):
        r = self.new_result()
        cols = ctx.shared.get("page_columns", {})
        checked = bad = severe = 0
        examples = []
        furniture_last = furniture_pages = 0
        for p in ctx.pages:
            stream = [l for l in p.lines if not l.vertical]
            if len(stream) < 12:
                continue
            ncol, gx = cols.get(p.number, (1, None))

            def key_of(i):
                l = stream[i]
                c = 0 if (ncol == 1 or gx is None or l.x0 < gx) else 1
                return (c, round(l.y0 / 3), l.x0)

            expected = sorted(range(len(stream)), key=key_of)
            rank = {idx: pos for pos, idx in enumerate(expected)}
            viol = sum(1 for i in range(len(stream) - 1) if rank[i + 1] < rank[i])
            rate = viol / (len(stream) - 1)
            checked += 1
            if rate > 0.02:
                bad += 1
                if rate > 0.10:
                    severe += 1
                if len(examples) < 5:
                    examples.append((p.number, round(rate, 2)))
            band_idx = [i for i, l in enumerate(stream) if ctx.band_of(l, p) == "header"]
            if band_idx:
                furniture_pages += 1
                if min(band_idx) > len(stream) / 2:
                    furniture_last += 1
        r.facts = {"pages_checked": checked, "pages_out_of_order": bad, "pages_badly_out_of_order": severe,
                   "examples": examples, "header_lines_after_body_in_stream": f"{furniture_last}/{furniture_pages}"}
        r.say(f"Raw stream order differs from the visual (column-major, top-to-bottom) order on {bad} of {checked} pages "
              f"({severe} badly). Running headers appear after the body text in the stream on {furniture_last} of "
              f"{furniture_pages} pages.")
        for pn, _rate in examples[:2]:
            ctx.add_evidence("stream_order_differs", pn, keep=2)
        if bad > 0.02 * max(1, checked):
            r.score("Reading order", "warn", "raw stream order is unreliable: always sort by (column, y)")
            r.note("Never trust the PDF stream order; sort lines by (column, y).")
        else:
            r.score("Reading order", "ok", "stream order matches visual order")
        r.recipe = {"sort_lines_by_position": bad > 0, "pages_out_of_order": bad}
        return r


# ======================================================================
# Analyzer: callout boxes and labelled blocks
# ======================================================================
@register
class BoxAnalyzer(Analyzer):
    key = "boxes"
    title = "Callout boxes and labelled blocks"
    requires = ("typography", "layout")

    def _container(self, ctx, p, ln, text_w):
        shapes = ctx.drawings(p.number)
        body = ctx.body_lines(p.number)
        page_bottom = max((l.y1 for l in body), default=p.height)
        enclosing = [s for s in shapes if s.fill and s.w >= 0.5 * text_w and s.h >= 30 and s.x0 <= ln.x0 + 2
                     and s.x1 >= ln.x1 - 2 and s.y0 <= ln.y0 + 2 and s.y1 >= ln.y1 - 2]
        icon = any(s.w <= 45 and s.h <= 45 and s.x1 <= ln.x0 + 4 and ln.x0 - s.x1 <= 45
                   and abs((s.y0 + s.y1) / 2 - (ln.y0 + ln.y1) / 2) <= 25 for s in shapes)
        if enclosing:
            s = min(enclosing, key=lambda s: s.area)
            continues = False
            if s.y1 >= page_bottom - 12 and p.number < len(ctx.pages):
                top = (ctx.shared.get("layout", {}).get("body_top") or 60.0) + 25
                continues = any(t.fill == s.fill and t.y0 <= top and t.w >= 0.5 * text_w and t.h >= 15
                                for t in ctx.drawings(p.number + 1))
            return {"kind": "filled_rect", "color": s.fill, "opacity": s.opacity, "y_start": s.y0, "y_end": s.y1,
                    "continues": continues, "icon": icon}
        rules = sorted((s for s in shapes if s.h <= 4 and s.w >= 0.6 * text_w and s.y0 >= ln.y0 - 12), key=lambda s: s.y0)
        opening = next((s for s in rules if s.y0 - ln.y1 <= 45), None)
        if opening:
            ocol = opening.fill or opening.stroke
            closing = next((s for s in rules if s.y0 > opening.y1 + 20 and (s.fill or s.stroke) == ocol), None)
            return {"kind": "rule_bounded", "color": ocol, "opacity": None, "y_start": ln.y0,
                    "y_end": closing.y1 if closing else page_bottom, "continues": closing is None, "icon": icon}
        stroked = [s for s in shapes if not s.fill and s.stroke and s.w >= 0.5 * text_w and s.h >= 30
                   and s.x0 <= ln.x0 + 2 and s.x1 >= ln.x1 - 2 and s.y0 <= ln.y0 + 2 and s.y1 >= ln.y1 - 2]
        if stroked:
            s = min(stroked, key=lambda s: s.area)
            return {"kind": "stroked_rect", "color": s.stroke, "opacity": None, "y_start": s.y0, "y_end": s.y1,
                    "continues": False, "icon": icon}
        return {"kind": "none", "color": None, "opacity": None, "y_start": ln.y0, "y_end": page_bottom,
                "continues": False, "icon": icon}

    def run(self, ctx):
        r = self.new_result()
        cfg = ctx.config
        body_sid, bsize = ctx.body_style_id, ctx.body_style.size
        outline_keys = {alnum_key(e.title) for e in ctx.entries}
        layout = ctx.shared.get("layout", {})
        left = layout.get("text_left") or 72.0
        cand = defaultdict(list)
        for p in ctx.pages:
            for ln in ctx.body_lines(p.number):
                t = ln.text
                if ln.vertical or not ln.pure or ln.dom == body_sid or not t.isupper():
                    continue
                if sum(c.isalpha() for c in t) < 3 or len(t.split()) > cfg.box_label_max_words:
                    continue
                if ctx.styles.get(ln.dom).size < bsize * 0.95 or alnum_key(t) in outline_keys or ctx.caption_re.match(t):
                    continue
                cand[t].append((p.number, ln))
        labels = {t: v for t, v in cand.items() if len(v) >= cfg.box_label_min_count}

        rows, facts = [], []
        label_lines_by_page = defaultdict(list)
        for text, occ in sorted(labels.items(), key=lambda kv: -len(kv[1])):
            step = max(1, len(occ) // cfg.box_label_sample_per_type)
            sample = occ[::step][:cfg.box_label_sample_per_type]
            kinds, colors, opac = Counter(), Counter(), Counter()
            icons = cont = 0
            insets, title_styles, title_examples = [], Counter(), []
            for pn, ln in sample:
                p = ctx.page(pn)
                label_lines_by_page[pn].append(ln)
                c = self._container(ctx, p, ln, layout.get("text_width") or 0.75 * p.width)
                kinds[c["kind"]] += 1
                if c["color"]:
                    colors[c["color"]] += 1
                if c["opacity"] is not None:
                    opac[round(c["opacity"], 2)] += 1
                icons += c["icon"]
                cont += c["continues"]
                inside = [l for l in ctx.body_lines(pn) if not l.vertical and l.y0 > ln.y1 - 1
                          and l.y0 < c["y_end"] and l is not ln]
                if inside:
                    insets.append(median([l.x0 for l in inside]) - left)
                    first = min(inside, key=lambda l: l.y0)
                    if first.y0 - ln.y1 < 25:
                        title_styles[ctx.styles.get(first.dom).label()] += 1
                        if len(title_examples) < 3:
                            title_examples.append(clip(first.text, 40))
            design = kinds.most_common(1)[0][0]
            n = len(sample)
            style = ctx.styles.get(occ[0][1].dom).label()
            pages = sorted({pn for pn, _ in occ})
            rows.append([text, len(occ), compress_ranges(pages[:6], 6), design, kinds.most_common(1)[0][1] and
                         (colors.most_common(1)[0][0] if colors else "-"), pct(icons, n, 0), pct(cont, n, 0),
                         f"{median(insets):.0f}pt" if insets else "-", title_styles.most_common(1)[0][0] if title_styles else "-"])
            facts.append({"label": text, "occurrences": len(occ), "pages_sample": pages[:12], "label_style": style,
                          "container": design, "container_mix": dict(kinds), "color": colors.most_common(1)[0][0] if colors else None,
                          "fill_opacity": dict(opac), "icon_share": round(ratio(icons, n), 2),
                          "continues_next_page_share": round(ratio(cont, n), 2),
                          "body_inset_pt": round(median(insets), 1) if insets else None,
                          "title_style": title_styles.most_common(1)[0][0] if title_styles else None,
                          "title_examples": title_examples})
            ctx.add_evidence(f"box_{alnum_key(text)[:24]}", occ[0][0], keep=1)

        # large shaded regions that carry no label (unlabelled callouts)
        unlabeled, colors_un = 0, Counter()
        text_w = layout.get("text_width") or 0.75 * ctx.pages[0].width
        for p in ctx.pages[::5][:150]:
            for s in ctx.drawings(p.number):
                if s.fill and s.w >= 0.6 * text_w and s.h >= 40 and s.area >= 15000:
                    has_label = any(s.x0 <= l.x0 + 2 and s.x1 >= l.x1 - 2 and s.y0 <= l.y0 + 2 and s.y1 >= l.y1 - 2
                                    for l in label_lines_by_page.get(p.number, []))
                    if not has_label and s.fill.lower() not in ("#ffffff",):
                        unlabeled += 1
                        colors_un[s.fill] += 1

        r.facts = {"box_types": facts, "unlabelled_shaded_regions_in_sample": unlabeled,
                   "unlabelled_colors": dict(colors_un.most_common(5))}
        if rows:
            r.say(f"{len(rows)} recurring all-caps label(s) found (>= {cfg.box_label_min_count} occurrences). "
                  "A box is recognised by its label; its extent comes from the container design column.")
            r.add_table("Recurring labels and how their boxes are drawn",
                        ["Label", "Occurrences", "Example pages", "Container design", "Colour", "Icon", "Continues on next page",
                         "Body inset", "Title style (line after label)"], rows,
                        note="Container design: filled_rect = shaded background rectangle; rule_bounded = coloured rule above and "
                             "below; stroked_rect = outlined box; none = label only (structural label, not a box).")
            designs = Counter(f["container"] for f in facts)
            r.say("Container designs in use: " + ", ".join(f"{k} x{v}" for k, v in designs.items()) + ".")
            if any(f["container"] == "none" and f["occurrences"] >= 10 for f in facts):
                r.info("Some recurring labels have no container (e.g. 'LEARNING OBJECTIVES'); they are structural labels "
                       "that open a block, not callout boxes.")
        else:
            r.say("No recurring all-caps box labels were found.")
        if unlabeled:
            r.info(f"{unlabeled} large shaded regions without a label were seen in the sampled pages "
                   f"(colours {dict(colors_un.most_common(3))}); they may be unlabelled callouts or table shading.")
        r.score("Boxes", "ok" if rows else "info", f"{len(rows)} labelled block type(s)" if rows else "none detected")
        r.recipe = {"box_types": [{"label": f["label"], "container": f["container"], "color": f["color"],
                                   "label_style": f["label_style"], "continues_pages": f["continues_next_page_share"] > 0.1}
                                  for f in facts]}
        for f in facts:
            how = {"filled_rect": "text lines inside the rectangle belong to the box; a box that runs past the page repeats as a new "
                                  "rectangle at the top of the next page",
                   "rule_bounded": "box starts at the label row and ends at the closing coloured rule (or the page end if it continues)",
                   "stroked_rect": "text lines inside the outlined rectangle belong to the box",
                   "none": "label only; the block ends at the next heading or label"}[f["container"]]
            r.note(f"{f['label']}: {f['container']} - {how}.")
        return r


# ======================================================================
# Analyzer: figures and captions
# ======================================================================
@register
class FigureAnalyzer(Analyzer):
    key = "figures"
    title = "Figures, images and captions"
    requires = ("typography", "layout")

    def run(self, ctx):
        r = self.new_result()
        bsize = ctx.body_style.size
        xref_pos = defaultdict(Counter)
        for p in ctx.pages:
            for im in p.images:
                if im.xref:
                    xref_pos[im.xref][(round(im.x0 / 10), round(im.y0 / 10))] += 1
        # a logo/watermark repeats at the same position; a figure reused elsewhere does not
        decorative = {x for x, c in xref_pos.items() if sum(c.values()) >= 5 and c.most_common(1)[0][1] >= 0.8 * sum(c.values())}
        total_images = sum(len(p.images) for p in ctx.pages)
        content_imgs = {p.number: [im for im in p.images if im.area >= 1500 and im.xref not in decorative]
                        for p in ctx.pages}
        n_content = sum(len(v) for v in content_imgs.values())
        icons = sum(1 for p in ctx.pages for im in p.images if im.area < 1500)
        caps = [c for c in ctx.captions if c[2] == "figure"]
        paired, below, above, unpaired = 0, 0, 0, []
        label_styles, cont_styles = Counter(), Counter()
        parts = scheme_ch = 0
        rows = []
        paired_imgs = set()
        for pn, ln, _k, label in caps:
            label_styles[ctx.styles.get(ln.spans[0].style).label()] += 1
            body = sorted((l for l in ctx.body_lines(pn) if not l.vertical), key=lambda l: (l.y0, l.x0))
            idx = next((i for i, l in enumerate(body) if l is ln), None)
            ncap_lines, cap_text = 1, ln.text
            if idx is not None:
                prev = ln
                for nxt in body[idx + 1: idx + 8]:
                    if nxt.y0 - prev.y1 <= 0.6 * max(prev.height, 6) and ctx.styles.get(nxt.dom).size < bsize * 0.95 \
                            and abs(nxt.x0 - ln.x0) < 30:
                        if ncap_lines == 1:
                            cont_styles[ctx.styles.get(nxt.dom).label()] += 1
                        ncap_lines += 1
                        cap_text += " " + nxt.text
                        prev = nxt
                    else:
                        break
            if re.search(r"\(a\)|\(b\)", cap_text):
                parts += 1
            if re.search(r"\d+[.\-\u2013]\d+", label):
                scheme_ch += 1
            best = None
            for im in content_imgs.get(pn, []):
                xo = min(ln.x1 + 200, im.x1) - max(ln.x0 - 200, im.x0)
                if xo < max(20, 0.3 * im.w):
                    continue
                d_below = ln.y0 - im.y1       # caption under the image
                d_above = im.y0 - ln.y1       # caption over the image
                for d, pos in ((d_below, "below"), (d_above, "above")):
                    if -5 <= d <= 45 and (best is None or abs(d) < best[0]):
                        best = (abs(d), pos, im)
            if best:
                paired += 1
                paired_imgs.add((pn, best[2].xref, round(best[2].y0)))
                below += best[1] == "below"
                above += best[1] == "above"
            else:
                unpaired.append((pn, label))
            if len(rows) < 12:
                rows.append([label, pn, best[1] if best else "no image found", f"{best[2].w:.0f}x{best[2].h:.0f}pt" if best else "-",
                             ncap_lines, clip(cap_text, 70)])
        vector_like = []
        for pn, label in unpaired[:60]:
            if len(ctx.drawings(pn)) >= 40:
                vector_like.append((pn, label))
        free_imgs = sum(1 for pn, ims in content_imgs.items() for im in ims if (pn, im.xref, round(im.y0)) not in paired_imgs)
        ctx.shared["figure_captions"] = len(caps)

        r.facts = {"captions": len(caps), "placed_images": total_images, "content_images": n_content,
                   "icons_or_tiny_images": icons, "decorative_repeated_images": len(decorative),
                   "captions_paired_with_image": paired, "caption_below_image": below, "caption_above_image": above,
                   "captions_without_image": len(unpaired), "images_without_caption": free_imgs,
                   "vector_figure_candidates": len(vector_like), "captions_with_a_b_parts": parts,
                   "chapter_dot_number_labels": scheme_ch, "label_styles": dict(label_styles.most_common(3)),
                   "continuation_styles": dict(cont_styles.most_common(3))}
        r.say(f"{len(caps)} figure captions and {n_content} content images "
              f"({icons} tiny images/icons and {len(decorative)} repeated decorative images ignored). "
              f"{paired} captions ({pct(paired, len(caps))}) sit within 45pt of an image "
              f"(caption below: {below}, above: {above}).")
        if caps:
            r.say(f"Caption label style: {label_styles.most_common(1)[0][0]}"
                  + (f"; continuation text style: {cont_styles.most_common(1)[0][0]}" if cont_styles else "")
                  + f". {parts} captions describe sub-parts (a), (b); the parts are drawn inside the image, so only the "
                    "caption text is searchable.")
            r.add_table("Sample figures", ["Label", "Page", "Caption position", "Image size", "Caption lines", "Caption text"], rows)
        if unpaired:
            r.info(f"{len(unpaired)} captions have no image next to them (first: "
                   + ", ".join(f"{l} p{p}" for p, l in unpaired[:4])
                   + f"); {len(vector_like)} of the checked ones sit on pages with many vector shapes, i.e. the figure is drawn "
                     "as vector graphics. Render the page region instead of extracting the embedded image.")
        if free_imgs:
            r.info(f"{free_imgs} content images have no caption (photos in boxes, unlabelled illustrations).")
        for pn, _l, _k, _lab in caps[:1]:
            ctx.add_evidence("figure_caption", pn)
        if caps:
            r.score("Figures", "ok" if paired >= 0.8 * len(caps) else "warn",
                    f"{pct(paired, len(caps))} of captions paired with an image")
        else:
            r.score("Figures", "info", "no figure captions detected")
        r.recipe = {"caption_regex": ctx.config.caption_pattern,
                    "caption_label_style": label_styles.most_common(1)[0][0] if label_styles else None,
                    "caption_continuation_style": cont_styles.most_common(1)[0][0] if cont_styles else None,
                    "caption_position": "below image" if below >= above else "above image",
                    "pairing_rule": "nearest content image within 45pt vertically that overlaps the caption horizontally",
                    "ignore_images_smaller_than_pt2": 1500, "decorative_xrefs": sorted(decorative)[:50],
                    "extract_as": "render the figure region (image + vector overlays) as PNG; do not reuse the raw embedded image"}
        r.note("Pair each caption with the nearest image above it (" + r.recipe["caption_position"] + "); render the region as PNG; "
               "index the caption plus the body sentences that cite the figure number.")
        return r


# ======================================================================
# Analyzer: tables
# ======================================================================
@register
class TableAnalyzer(Analyzer):
    key = "tables"
    title = "Tables"
    requires = ("typography", "layout")

    def run(self, ctx):
        r = self.new_result()
        cfg = ctx.config
        caps = [c for c in ctx.captions if c[2] == "table"]
        pages = sorted({c[0] for c in caps})
        if cfg.deep_tables:
            pages = sorted(set(pages) | set(range(1, len(ctx.pages) + 1)))
        pages = pages[:cfg.max_table_pages]
        found, rows, pos_c, imgs_in = [], [], Counter(), 0
        unsupported = False
        title_styles = Counter()
        for pno in pages:
            try:
                tabs = ctx.doc[pno - 1].find_tables().tables
            except AttributeError:
                unsupported = True
                break
            except Exception:
                continue
            page_caps = [c for c in caps if c[0] == pno]
            body = [l for l in ctx.body_lines(pno) if not l.vertical]
            for t in tabs:
                x0, y0, x1, y1 = t.bbox
                best = None
                for _pn, cl, _k, label in page_caps:
                    for d, pos in ((y0 - cl.y1, "caption above"), (cl.y0 - y1, "label below")):
                        if -5 <= d <= 60 and (best is None or abs(d) < best[0]):
                            best = (abs(d), pos, label)
                above = [l for l in body if l.y1 <= y0 + 2 and y0 - l.y1 <= 30]
                if above:
                    top_line = max(above, key=lambda l: l.y1)
                    title_styles[ctx.styles.get(top_line.dom).label()] += 1
                n_img = sum(1 for im in ctx.page(pno).images if x0 <= (im.x0 + im.x1) / 2 <= x1 and y0 <= (im.y0 + im.y1) / 2 <= y1)
                imgs_in += n_img
                pos_c[best[1] if best else "no caption nearby"] += 1
                found.append(pno)
                if len(rows) < 12:
                    rows.append([pno, best[2] if best else "-", best[1] if best else "-", f"{t.row_count}x{t.col_count}",
                                 f"({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})", n_img])
        pages_found = len(set(found))
        r.facts = {"table_captions": len(caps), "caption_pages": len({c[0] for c in caps}), "pages_scanned": len(pages),
                   "tables_detected": len(found), "pages_with_detected_table": pages_found,
                   "caption_position": dict(pos_c), "images_inside_tables": imgs_in,
                   "title_style_above_table": dict(title_styles.most_common(3)), "deep_scan": cfg.deep_tables}
        if unsupported:
            r.warn("This PyMuPDF version has no find_tables(); upgrade PyMuPDF (>= 1.23) for table detection.")
        r.say(f"{len(caps)} table captions on {len({c[0] for c in caps})} pages; ruling-based detection found {len(found)} tables "
              f"on {pages_found} of the {len(pages)} scanned pages"
              + ("" if cfg.deep_tables else " (only caption pages scanned; use --deep-tables to scan more)") + ".")
        if rows:
            r.add_table("Sample detected tables", ["Page", "Caption", "Caption position", "Rows x cols", "Bounding box", "Images inside"], rows)
        if pos_c:
            r.say("Caption/label position relative to the table: " + ", ".join(f"{k} x{v}" for k, v in pos_c.items()) + ".")
        if imgs_in:
            r.info(f"{imgs_in} images sit inside detected tables (e.g. structural formulas); keep the table as text and "
                   "also render the table region as an image.")
        if caps and pages_found < 0.6 * len({c[0] for c in caps}):
            r.warn("Table detection found fewer tables than captions: some tables have no ruling lines. "
                   "Fall back to text-position clustering or render the region and let a vision model read it.")
        for pn in found[:1]:
            ctx.add_evidence("table_page", pn)
        r.score("Tables", "ok" if (not caps or pages_found >= 0.6 * len({c[0] for c in caps})) else "warn",
                f"{len(found)} tables detected for {len(caps)} captions")
        r.recipe = {"table_detection": "page.find_tables() on pages with a table caption",
                    "caption_position": dict(pos_c), "title_style_above_table": title_styles.most_common(1)[0][0] if title_styles else None,
                    "store_as": "markdown/CSV text of the table + rendered image + caption"}
        r.note("Exclude table cell text from paragraph building; store each table as its own block with caption and page.")
        return r


# ======================================================================
# Analyzer: glossary / key-term format
# ======================================================================
def _sample(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _column_ordered(ctx, pno):
    lines = [l for l in ctx.body_lines(pno) if not l.vertical]
    ncol, gx = ctx.shared.get("page_columns", {}).get(pno, (1, None))
    if ncol == 2 and gx:
        left = sorted((l for l in lines if l.x0 < gx), key=lambda l: l.y0)
        right = sorted((l for l in lines if l.x0 >= gx), key=lambda l: l.y0)
        return left + right
    return sorted(lines, key=lambda l: (l.y0, l.x0))


@register
class GlossaryFormatAnalyzer(Analyzer):
    key = "glossary_format"
    title = "Key-terms / glossary format"
    requires = ("page_coverage", "layout")

    def run(self, ctx):
        r = self.new_result()
        pe = ctx.shared.get("page_entry", {})
        pages = [pn for pn, idx in pe.items() if ctx.entries[idx].kind in ("key_terms", "glossary")]
        if not pages:
            r.say("No key-terms or glossary sections in the outline.")
            r.score("Key terms", "info", "none")
            return r
        pages = _sample(pages, 40)
        term_lines = cont_lines = 0
        term_x, cont_x = [], []
        seps, defs_len, examples, per_page = Counter(), [], [], []
        for pn in pages:
            lines = _column_ordered(ctx, pn)
            entry = None
            n_terms = 0
            for ln in lines:
                first = ctx.styles.get(ln.spans[0].style)
                mixed = first.bold and len(ln.spans) > 1 and not ln.pure
                short_bold = first.bold and ln.pure and len(ln.text.split()) <= 8
                if mixed or short_bold:
                    lead = ""
                    for sp in ln.spans:
                        if ctx.styles.get(sp.style).bold:
                            lead += sp.text
                        else:
                            break
                    rest = ln.text[len(lead.strip()):].strip()
                    term_lines += 1
                    n_terms += 1
                    term_x.append(ln.x0)
                    seps[rest[:1] if rest[:1] in ":\u2014\u2013-;." else "(space only)"] += 1
                    if entry and len(examples) < 4:
                        examples.append(entry)
                    entry = [lead.strip(), rest]
                else:
                    cont_lines += 1
                    cont_x.append(ln.x0)
                    if entry:
                        entry[1] += " " + ln.text
            if entry and len(examples) < 4:
                examples.append(entry)
            per_page.append(n_terms)
        for e in examples:
            defs_len.append(len(e[1]))
        hang = (median(cont_x) - median(term_x)) if term_x and cont_x else None
        r.facts = {"pages_sampled": len(pages), "term_lines": term_lines, "continuation_lines": cont_lines,
                   "terms_per_page_median": median(per_page), "term_x": median(term_x), "continuation_x": median(cont_x),
                   "hanging_indent_pt": hang, "separator_after_term": dict(seps)}
        if not term_lines:
            r.say(f"Sampled {len(pages)} pages but found no line that starts with a bold term; the key-terms format is "
                  "different from 'bold term + definition'. Inspect the evidence page.")
            r.score("Key terms", "warn", "term/definition structure not recognised")
            return r
        r.say(f"Sampled {len(pages)} pages. A term entry starts with a bold span at x~{median(term_x) or 0:.0f}pt"
              + (f"; continuation lines are indented by {hang:.0f}pt" if hang else "")
              + f"; separator after the term: {dict(seps)}; about {median(per_page) or 0:.0f} entries per page.")
        r.add_table("Parsed sample entries", ["Term", "Definition (start)"], [[e[0], clip(e[1], 90)] for e in examples])
        r.score("Key terms", "ok", f"term/definition structure recognised ({term_lines} term lines in sample)")
        r.recipe = {"entry_start": "line whose first span is bold", "continuation": "lines without a leading bold span",
                    "term_x": median(term_x), "hanging_indent_pt": hang, "separator": dict(seps)}
        r.note("Parse key terms as (bold lead-in = term, remaining text = definition); continuation lines extend the definition.")
        if pages:
            ctx.add_evidence("key_terms_page", pages[0], keep=1)
        return r


# ======================================================================
# Analyzer: review / critical-thinking question format
# ======================================================================
@register
class QuestionFormatAnalyzer(Analyzer):
    key = "question_format"
    title = "Question sections format"
    requires = ("page_coverage", "layout")

    def run(self, ctx):
        r = self.new_result()
        pe = ctx.shared.get("page_entry", {})
        kinds = ("review_questions", "critical_thinking", "interactive_link_questions")
        per_kind = defaultdict(list)
        for pn, idx in pe.items():
            if ctx.entries[idx].kind in kinds:
                per_kind[ctx.entries[idx].kind].append(pn)
        has_key = any(e.kind == "answer_key" for e in ctx.entries)
        if not per_kind:
            r.say("No question sections in the outline.")
            r.score("Questions", "info", "none")
            return r
        rows, facts = [], {}
        for kind, pages in per_kind.items():
            pages = _sample(sorted(pages), 30)
            q_lines = opt_lines = ans_lines = bold_q = blank = tf = 0
            q_x, opt_x, nums = [], [], []
            for pn in pages:
                for ln in _column_ordered(ctx, pn):
                    m = NUMBERED_RE.match(ln.text)
                    if m:
                        q_lines += 1
                        q_x.append(ln.x0)
                        nums.append(int(m.group(1)))
                        bold_q += ctx.styles.get(ln.spans[0].style).bold
                    elif LETTERED_RE.match(ln.text):
                        opt_lines += 1
                        opt_x.append(ln.x0)
                    if re.match(r"^\s*(answer|ans\.?)\b", ln.text, re.I):
                        ans_lines += 1
                    blank += "____" in ln.text or "\u2026\u2026" in ln.text
                    tf += bool(re.search(r"\btrue\b.*\bfalse\b|\bfalse\b.*\btrue\b", ln.text, re.I))
            facts[kind] = {"pages_sampled": len(pages), "numbered_question_lines": q_lines, "option_lines": opt_lines,
                           "answer_lines": ans_lines, "question_lines_bold_share": round(ratio(bold_q, q_lines), 2),
                           "fill_in_blank": blank, "true_false_hints": tf, "question_x": median(q_x), "option_x": median(opt_x),
                           "number_range_in_sample": [min(nums), max(nums)] if nums else None}
            rows.append([kind, len(pages), q_lines, opt_lines, ans_lines, pct(bold_q, q_lines, 0),
                         f"{median(q_x):.0f}/{median(opt_x):.0f}" if q_x and opt_x else "-"])
        r.facts = {"by_kind": facts, "answer_key_in_outline": has_key}
        r.add_table("Question formats (sampled pages)", ["Section kind", "Pages", "Numbered questions", "a./b./c. options",
                                                         "'Answer' lines", "Bold question text", "x question/option"], rows)
        r.say("Questions are numbered lines (pattern 'N.'), multiple-choice options are lettered lines. "
              + ("An answer key section exists in the outline." if has_key else
                 "NO answer key section exists in the outline and no 'Answer' lines were found in the sampled pages: "
                 "these questions cannot be auto-graded from this PDF; use them as practice prompts or retrieval test queries."
                 if not any(f["answer_lines"] for f in facts.values()) else "Some 'Answer' lines were found; inspect them."))
        r.score("Questions", "info", "answers available" if has_key else "questions without answers")
        r.recipe = {"question_regex": NUMBERED_RE.pattern, "option_regex": LETTERED_RE.pattern,
                    "answers_available": has_key or any(f["answer_lines"] for f in facts.values())}
        r.note("Split question blocks at numbered lines; options are lettered lines at a deeper indent; store as quiz_source only.")
        return r


# ======================================================================
# Analyzer: hyperlinks and cross-references
# ======================================================================
@register
class LinksAnalyzer(Analyzer):
    key = "links"
    title = "Hyperlinks and cross-references"
    requires = ("overview",)

    def run(self, ctx):
        r = self.new_result()
        uri = goto = other = 0
        domains, pages_with = Counter(), set()
        for i in range(len(ctx.pages)):
            try:
                links = ctx.doc[i].get_links()
            except Exception:
                continue
            for lk in links:
                k = lk.get("kind")
                if k == 2:
                    uri += 1
                    pages_with.add(i + 1)
                    host = urlparse(lk.get("uri", "")).netloc.lower()
                    if host:
                        domains[host] += 1
                elif k == 1:
                    goto += 1
                else:
                    other += 1
        r.facts = {"external_links": uri, "internal_links": goto, "other_links": other,
                   "pages_with_external_links": len(pages_with), "top_domains": dict(domains.most_common(8))}
        r.say(f"{uri} external links on {len(pages_with)} pages (top domains: {dict(domains.most_common(4))}); "
              f"{goto} internal links (cross-references that point to a page/figure/section).")
        if goto:
            r.info("Internal links can be used to resolve 'Figure 1.6' / 'Section 2.3' references to exact targets.")
        r.score("Links", "info", f"{uri} external, {goto} internal")
        r.recipe = {"keep_external_urls": uri > 0, "resolve_internal_links": goto > 0}
        return r


# ======================================================================
# Analyzer: encoding, special characters and fonts
# ======================================================================
@register
class EncodingFontAnalyzer(Analyzer):
    key = "encoding_fonts"
    title = "Text encoding and fonts"
    requires = ("overview",)

    def run(self, ctx):
        r = self.new_result()
        cat = Counter()
        nonascii = Counter()
        total = 0
        cid = 0
        for p in ctx.pages:
            for ln in p.lines:
                t = ln.text
                total += len(t)
                if "(cid:" in t:
                    cid += 1
                for ch in t:
                    o = ord(ch)
                    if o > 127:
                        nonascii[ch] += 1
                        if 0xE000 <= o <= 0xF8FF:
                            cat["private_use"] += 1
                        elif o == 0xFFFD:
                            cat["replacement_char"] += 1
                        elif 0xFB00 <= o <= 0xFB06:
                            cat["ligature"] += 1
                        elif o in (0x00A0, 0x2009, 0x202F, 0x2002, 0x2003):
                            cat["special_space"] += 1
                        elif o in (0x200B, 0x200C, 0x200D, 0xFEFF):
                            cat["zero_width"] += 1
                        elif o == 0x00AD:
                            cat["soft_hyphen"] += 1
                        elif 0x0370 <= o <= 0x03FF:
                            cat["greek"] += 1
                        elif 0x2200 <= o <= 0x22FF or 0x2190 <= o <= 0x21FF:
                            cat["math_arrows"] += 1
                    elif o < 32 and ch not in "\t\n":
                        cat["control"] += 1
        fonts = Counter()
        for p in ctx.pages:
            for ln in p.lines:
                for sp in ln.spans:
                    fonts[ctx.styles.get(sp.style).font] += len(sp.text)
        info = {}
        step = max(1, len(ctx.pages) // 40)
        for i in range(0, len(ctx.pages), step):
            try:
                for f in ctx.doc.get_page_fonts(i):
                    name = _SUBSET_PREFIX.sub("", f[3])
                    info.setdefault(name, {"type": f[2], "embedded": f[1] not in ("n/a", "")})
            except Exception:
                pass
        math_fonts = [f for f in fonts if re.search(r"(math|symbol|cmsy|cmmi|cmex|stix|euclid)", f, re.I)]
        rows = [[f, c, info.get(f, {}).get("type", "?"), "yes" if info.get(f, {}).get("embedded") else "?"]
                for f, c in fonts.most_common(15)]
        r.facts = {"characters": total, "categories": dict(cat), "cid_placeholders": cid,
                   "top_non_ascii": {f"U+{ord(c):04X} {unicodedata.name(c, '?')}": n for c, n in nonascii.most_common(15)},
                   "math_fonts": math_fonts}
        r.add_table("Fonts by amount of text", ["Font", "Characters", "Type", "Embedded"], rows)
        r.add_table("Most common non-ASCII characters", ["Character", "Code point / name", "Count"],
                    [[c, f"U+{ord(c):04X} {unicodedata.name(c, '?')}", n] for c, n in nonascii.most_common(12)])
        bad = cat["private_use"] + cat["replacement_char"] + cid
        r.say(f"{len(nonascii)} distinct non-ASCII characters; categories: {dict(cat)}.")
        if bad > 0.001 * max(1, total):
            r.problem(f"{bad} characters look broken (private-use area, replacement char or '(cid:)' placeholders). "
                      "The PDF fonts have no proper text mapping; OCR or a different extractor may be needed.")
            r.score("Encoding", "fail", f"{bad} broken characters")
        else:
            r.score("Encoding", "ok", "no broken character mapping detected")
        if cat["ligature"]:
            r.info(f"{cat['ligature']} ligature characters (fi, fl, ...) must be expanded to plain letters.")
        if cat["special_space"] or cat["zero_width"] or cat["soft_hyphen"]:
            r.info("Special spaces / zero-width / soft-hyphen characters present: normalise whitespace and strip them.")
        if math_fonts:
            r.info(f"Math/symbol fonts in use ({', '.join(math_fonts[:3])}): equations may not extract as plain text.")
        r.recipe = {"normalise_unicode": "NFKC", "expand_ligatures": cat["ligature"] > 0,
                    "strip_chars": ["U+00AD", "U+200B", "U+FEFF"], "broken_characters": bad, "math_fonts": math_fonts}
        r.note("Normalise text with NFKC, replace special spaces by ' ', remove soft hyphens and zero-width characters.")
        return r


# ======================================================================
# Analyzer: content volume per section (chunking guidance)
# ======================================================================
@register
class ContentVolumeAnalyzer(Analyzer):
    key = "content_volume"
    title = "Content volume per section (chunking guidance)"
    requires = ("heading_locator",)

    def run(self, ctx):
        r = self.new_result()
        cuts = ctx.shared.get("cuts", [])
        keys = [(c[0], c[1]) for c in cuts]
        chars = Counter()
        before = 0
        for p in ctx.pages:
            for ln in ctx.body_lines(p.number):
                if ln.vertical:
                    continue
                i = bisect.bisect_right(keys, (p.number, ln.y0 + 0.5)) - 1
                if i < 0:
                    before += len(ln.text)
                else:
                    chars[cuts[i][2]] += len(ln.text)
        entries = ctx.entries
        leaf = [e for e in entries if not any(x.parent == e.index for x in entries)]
        by_kind = defaultdict(lambda: [0, 0])
        for e in entries:
            by_kind[e.kind][0] += 1
            by_kind[e.kind][1] += chars.get(e.index, 0)
        tot = sum(chars.values()) + before
        r.add_table("Text volume by section kind", ["Kind", "Entries", "Characters", "Est. tokens", "Share"],
                    [[k, a, f"{c:,}", f"{est_tokens(c):,}", pct(c, tot)] for k, (a, c) in
                     sorted(by_kind.items(), key=lambda kv: -kv[1][1])])
        teaching_leaf = [e for e in leaf if e.use == "teaching"]
        toks = [est_tokens(chars.get(e.index, 0)) for e in teaching_leaf]
        buckets = Counter()
        for t in toks:
            buckets["< 100" if t < 100 else "100-400" if t < 400 else "400-1000" if t < 1000 else
                    "1000-2500" if t < 2500 else "> 2500"] += 1
        order = ["< 100", "100-400", "400-1000", "1000-2500", "> 2500"]
        r.add_table("Teaching leaf sections by size (est. tokens)", ["Size bucket", "Sections"],
                    [[b, buckets.get(b, 0)] for b in order])
        largest = sorted(teaching_leaf, key=lambda e: -chars.get(e.index, 0))[:8]
        r.add_table("Largest teaching sections", ["Page", "Title", "Est. tokens"],
                    [[e.page, clip(e.title, 60), f"{est_tokens(chars.get(e.index, 0)):,}"] for e in largest])
        r.facts = {"total_body_chars": tot, "total_est_tokens": est_tokens(tot),
                   "teaching_leaf_sections": len(teaching_leaf),
                   "teaching_tokens": {"median": median(toks), "p90": percentile(toks, 0.9), "max": max(toks) if toks else None},
                   "size_buckets": dict(buckets),
                   "tokens_by_kind": {k: est_tokens(c) for k, (_a, c) in by_kind.items()},
                   "per_entry_tokens": {str(e.index): est_tokens(chars.get(e.index, 0)) for e in entries}}
        if toks:
            r.say(f"Body text totals about {est_tokens(tot):,} tokens. Teaching leaf sections: median {median(toks):.0f} tokens, "
                  f"90th percentile {percentile(toks, 0.9):.0f}, largest {max(toks):,}. "
                  f"{buckets.get('1000-2500', 0) + buckets.get('> 2500', 0)} sections exceed 1,000 tokens and need splitting; "
                  f"{buckets.get('< 100', 0)} are under 100 tokens and should be merged with a neighbour.")
        r.score("Content volume", "ok" if toks else "info", f"~{est_tokens(tot):,} tokens of body text")
        r.recipe = {"est_total_tokens": est_tokens(tot), "median_teaching_section_tokens": median(toks),
                    "split_sections_over_tokens": 1000, "merge_sections_under_tokens": 100,
                    "suggested_chunk_tokens": [300, 500], "suggested_overlap_tokens": 40}
        r.note("Chunk inside sections (never across a section cut) at roughly 300-500 tokens; sections above ~1,000 tokens "
               "split at sub-headings or paragraph breaks; sections under ~100 tokens merge with the previous one.")
        return r


# ======================================================================
# Report model and renderers (Strategy pattern: add a Renderer to add an output)
# ======================================================================
@dataclass
class BookReport:
    file: str
    generated: str
    version: str
    seconds: float
    results: list
    evidence: dict
    config: dict
    printed_offset: Optional[int] = None

    def result(self, key: str) -> Optional[AnalysisResult]:
        return next((r for r in self.results if r.key == key), None)

    @property
    def scorecard(self) -> list:
        return [(lbl, st, det) for r in self.results for (lbl, st, det) in r.scorecard]

    @property
    def verdict(self) -> str:
        sc = self.scorecard
        fails = sum(1 for _l, s, _d in sc if s == "fail")
        warns = sum(1 for _l, s, _d in sc if s == "warn")
        if fails:
            return "NEEDS ATTENTION"
        if warns >= 3:
            return "USABLE WITH CAUTION"
        return "READY FOR OUTLINE-DRIVEN INGESTION"


class Renderer(ABC):
    filename: ClassVar[str] = ""

    @abstractmethod
    def render(self, report: BookReport, cfg: AnalyzerConfig) -> str:
        ...


class MarkdownRenderer(Renderer):
    filename = "report.md"

    def render(self, report, cfg):
        out = []
        w = out.append
        w(f"# Textbook analysis: {report.file}")
        w(f"_Generated {report.generated} by text-book-analyser {report.version} in {report.seconds:.0f}s._")
        w("")
        w("This report describes how this specific PDF is built, so that the RAG ingestion pipeline can be written from "
          "facts instead of guesses. Sections 1-3 are the summary (verdict, problems, extraction recipe); section 4 has the "
          "evidence; section 5 lists pages to open in a PDF viewer to check the findings. `profile.json` holds the same "
          "data in machine-readable form.")
        w("")
        w("## 1. Verdict")
        w("")
        w(f"**{report.verdict}**")
        w("")
        w("| Check | Status | Detail |")
        w("|---|---|---|")
        for lbl, st, det in report.scorecard:
            w(f"| {md_cell(lbl)} | {st.upper()} | {md_cell(det)} |")
        w("")

        w("## 2. Needs attention")
        w("")
        problems = [f for r in report.results for f in r.findings if f.severity == "problem"]
        warnings = [f for r in report.results for f in r.findings if f.severity == "warning"]
        if not problems and not warnings:
            w("Nothing needs attention.")
        for title, items in (("Problems (must be solved before ingestion)", problems), ("Warnings", warnings)):
            if items:
                w(f"**{title}**")
                w("")
                for f in items:
                    w(f"- [{f.analyzer}] {f.message}")
                w("")
        w("")

        w("## 3. Extraction recipe (draft rules derived from this book)")
        w("")
        w("Each line is a rule the ingestion code should implement for this book. Rules marked *inferred* in the details "
          "should be confirmed by opening the evidence pages.")
        w("")
        for r in report.results:
            if r.recipe_notes:
                w(f"**{r.title}**")
                w("")
                for note in r.recipe_notes:
                    w(f"- {note}")
                w("")

        w("## 4. Detailed findings")
        w("")
        for i, r in enumerate(report.results, 1):
            w(f"### 4.{i} {r.title}")
            w("")
            for s in r.summary:
                w(s)
                w("")
            for t in r.tables:
                w(f"**{t.title}**")
                w("")
                if t.headers and t.rows:
                    w("| " + " | ".join(md_cell(h) for h in t.headers) + " |")
                    w("|" + "---|" * len(t.headers))
                    for row in t.rows[:cfg.table_rows_in_report]:
                        w("| " + " | ".join(md_cell(c) for c in row) + " |")
                    if len(t.rows) > cfg.table_rows_in_report:
                        w("")
                        w(f"_... {len(t.rows) - cfg.table_rows_in_report} more rows (see profile.json)._")
                else:
                    w("_(no rows)_")
                if t.note:
                    w("")
                    w(f"_{t.note}_")
                w("")
            infos = [f for f in r.findings if f.severity == "info"]
            for f in infos:
                w(f"- Note: {f.message}")
            if infos:
                w("")
            if r.failed:
                w("**This analyser crashed; details are in profile.json.**")
                w("")

        w("## 5. Evidence pages")
        w("")
        w("Open these PDF pages to check the findings by eye. 'Printed' is the page number printed on the page "
          "(from the detected offset); samples\\ contains line-level dumps of the first page of each reason.")
        w("")
        w("| Reason | PDF pages | Printed pages |")
        w("|---|---|---|")
        off = report.printed_offset
        for reason, pages in sorted(report.evidence.items()):
            printed = ", ".join(str(p - off) for p in pages) if off is not None else "-"
            w(f"| {md_cell(reason)} | {', '.join(map(str, pages))} | {printed} |")
        w("")
        w("## 6. Glossary of terms used in this report")
        w("")
        for term, text in (
                ("Standalone line", "a text line made (almost) entirely of one style, e.g. a heading"),
                ("Dominant style", "the style that covers most characters of a line"),
                ("Outline-confirmed", "a heading style proven by matching bookmark titles to page text"),
                ("Inferred", "a guess from statistics (size, colour, line length); verify on the evidence pages"),
                ("Band", "the strip at the top/bottom of a page that contains running headers/footers"),
                ("Cut point", "the (page, y) position where a section begins; sections can start mid-page"),
                ("Container design", "how a callout box is drawn: filled rectangle, rules, outline or label only")):
            w(f"- **{term}**: {text}")
        return "\n".join(out) + "\n"


class JsonRenderer(Renderer):
    filename = "profile.json"

    def render(self, report, cfg):
        data = {
            "file": report.file, "generated": report.generated, "analyser_version": report.version,
            "verdict": report.verdict, "scorecard": [{"check": l, "status": s, "detail": d} for l, s, d in report.scorecard],
            "recipe": {r.key: r.recipe for r in report.results if r.recipe},
            "evidence_pages": report.evidence,
            "findings": [vars(f) for r in report.results for f in r.findings],
            "analyzers": {r.key: {
                "title": r.title, "seconds": r.seconds, "failed": r.failed, "summary": r.summary, "facts": r.facts,
                "tables": [{"title": t.title, "headers": t.headers, "rows": t.rows, "note": t.note} for t in r.tables],
                "recipe_notes": r.recipe_notes} for r in report.results},
        }
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)


RENDERERS: list = [MarkdownRenderer(), JsonRenderer()]


# ======================================================================
# Evidence page dumps
# ======================================================================
def export_samples(ctx: BookContext, out_dir: Path, max_samples: int):
    chosen: dict[int, list] = {}
    for reason, pages in ctx.evidence.items():
        for pn in pages[:1]:
            chosen.setdefault(pn, []).append(reason)
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = ctx.shared.get("page_columns", {})
    for pn, reasons in sorted(chosen.items())[:max_samples]:
        p = ctx.page(pn)
        ncol, gx = cols.get(pn, (1, None))
        lines_out = [f"PDF page {pn} | {p.width:.0f}x{p.height:.0f}pt | kind: {ctx.kind_of_page(pn)} | "
                     f"columns: {ncol}" + (f" (gutter x~{gx})" if gx else "") + f" | reasons: {', '.join(reasons)}",
                     f"images: {[(round(i.x0), round(i.y0), round(i.x1), round(i.y1)) for i in p.images]}"]
        shapes = sorted(ctx.drawings(pn), key=lambda s: -s.area)[:25]
        for s in shapes:
            lines_out.append(f"shape {s.kind} fill={s.fill} stroke={s.stroke} opacity={s.opacity} "
                             f"bbox=({s.x0:.0f},{s.y0:.0f},{s.x1:.0f},{s.y1:.0f})")
        lines_out.append("--- lines in column-aware reading order: y | x0 | band | style (dominant) | text ---")
        ordered = _column_ordered_all(ctx, p, ncol, gx)
        for ln in ordered:
            band = ctx.band_of(ln, p) or "-"
            lines_out.append(f"y={ln.y0:6.1f} x={ln.x0:6.1f} {band:6} {ctx.styles.get(ln.dom).label()} "
                             f"{'(mixed) ' if not ln.pure else ''}| {ln.text}")
        name = re.sub(r"[^A-Za-z0-9_+.-]+", "_", "+".join(reasons))[:70]
        (out_dir / f"page_{pn:04d}__{name}.txt").write_text("\n".join(lines_out), encoding="utf-8")


def _column_ordered_all(ctx, p, ncol, gx):
    lines = list(p.lines)
    if ncol == 2 and gx:
        left = sorted((l for l in lines if l.x0 < gx), key=lambda l: (l.y0, l.x0))
        right = sorted((l for l in lines if l.x0 >= gx), key=lambda l: (l.y0, l.x0))
        head = [l for l in left if ctx.band_of(l, p) == "header"]
        return head + [l for l in left if l not in head] + right
    return sorted(lines, key=lambda l: (l.y0, l.x0))


# ======================================================================
# Orchestration
# ======================================================================
def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "book"


def analyze_book(pdf: Path, cfg: AnalyzerConfig, out_root: Path) -> dict:
    t0 = time.time()
    print(f"\n=== {pdf.name}")
    ctx = BookContext(pdf, cfg)
    if ctx.doc.needs_pass:
        print("  encrypted PDF, skipped")
        return {"file": pdf.name, "verdict": "SKIPPED (encrypted)", "pages": ctx.total_pages}
    print(f"  reading {ctx.total_pages} pages ...")
    ctx.load()
    results = run_analyzers(ctx, _REGISTRY)
    report = BookReport(pdf.name, datetime.now().strftime("%Y-%m-%d %H:%M"), ANALYSER_VERSION,
                        time.time() - t0, results, {k: v for k, v in ctx.evidence.items() if v}, {},
                        ctx.shared.get("dominant_offset"))
    out_dir = out_root / safe_name(pdf.stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    for rd in RENDERERS:
        (out_dir / rd.filename).write_text(rd.render(report, cfg), encoding="utf-8")
    if cfg.dump_samples:
        export_samples(ctx, out_dir / "samples", cfg.max_samples)
    ov = report.result("overview")
    hl = report.result("heading_locator")
    lay = report.result("layout")
    bx = report.result("boxes")
    summary = {
        "file": pdf.name, "verdict": report.verdict, "pages": ctx.total_pages,
        "tokens": (ov.facts.get("est_tokens") if ov else None),
        "outline_entries": len(ctx.entries),
        "headings_found": (f"{hl.facts['found_on_target_page'] + hl.facts['found_on_adjacent_page']}/{hl.facts['entries_checked']}"
                           if hl and hl.facts else "-"),
        "two_column_pages": lay.facts.get("two_column_pages") if lay and lay.facts else "-",
        "box_types": len(bx.facts.get("box_types", [])) if bx and bx.facts else "-",
        "folder": out_dir.name, "seconds": round(time.time() - t0),
    }
    print(f"  done in {summary['seconds']}s -> {out_dir}")
    return summary


def write_index(out_root: Path, summaries: list):
    lines = ["# Analysed books", "", f"_Updated {datetime.now().strftime('%Y-%m-%d %H:%M')}_", "",
             "| Book | Verdict | Pages | Est. tokens | Outline entries | Headings found | Two-column pages | Box types | Report |",
             "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(f"| {md_cell(s['file'])} | {s['verdict']} | {s.get('pages', '-')} | {s.get('tokens', '-')} | "
                     f"{s.get('outline_entries', '-')} | {s.get('headings_found', '-')} | {s.get('two_column_pages', '-')} | "
                     f"{s.get('box_types', '-')} | {s.get('folder', '-')}/report.md |")
    (out_root / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_books(args_books: list) -> list:
    if not args_books:
        return sorted(BOOKS_DIR.glob("*.pdf"))
    found = []
    for b in args_books:
        cands = [Path(b), BOOKS_DIR / b, BOOKS_DIR / (b + ".pdf")]
        hit = next((c for c in cands if c.is_file()), None)
        if hit is None:
            print(f"  not found: {b}")
        else:
            found.append(hit)
    return found


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    ap = argparse.ArgumentParser(description="Analyse textbook PDFs and write a report for building a RAG pipeline.")
    ap.add_argument("books", nargs="*", help="PDF path or file name inside test-books (default: all PDFs there)")
    ap.add_argument("--out", default=str(RESULTS_DIR), help="output folder (default: analysis-result)")
    ap.add_argument("--config", help="JSON file overriding AnalyzerConfig fields")
    ap.add_argument("--max-pages", type=int, default=0, help="analyse only the first N pages (quick test)")
    ap.add_argument("--no-samples", action="store_true", help="do not write evidence page dumps")
    ap.add_argument("--deep-tables", action="store_true", help="run table detection on many pages, not just caption pages")
    ap.add_argument("--list-analyzers", action="store_true")
    args = ap.parse_args(argv)

    if args.list_analyzers:
        for c in resolve_order(_REGISTRY):
            print(f"{c.key:20} requires={list(c.requires)}  {c.title}")
        return 0

    cfg = AnalyzerConfig.from_json(Path(args.config)) if args.config else AnalyzerConfig()
    if args.max_pages:
        cfg.max_pages = args.max_pages
    if args.no_samples:
        cfg.dump_samples = False
    if args.deep_tables:
        cfg.deep_tables = True

    BOOKS_DIR.mkdir(exist_ok=True)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    books = resolve_books(args.books)
    if not books:
        print(f"No PDFs found. Put books in: {BOOKS_DIR}")
        return 1
    summaries = []
    for pdf in books:
        try:
            summaries.append(analyze_book(pdf, cfg, out_root))
        except Exception as exc:
            print(f"  FAILED: {exc!r}")
            traceback.print_exc()
            summaries.append({"file": pdf.name, "verdict": f"FAILED ({exc!r})"})
    write_index(out_root, summaries)
    print(f"\nFinished. Open {out_root / 'index.md'} and the report.md inside each book folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())