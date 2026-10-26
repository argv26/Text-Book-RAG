"""Step 3: PDF -> clean text lines per page, in reading order.

Reads its rules from profile.json (recipe section) so the same code serves every book.
Output: output/<book>/pages.jsonl, one JSON record per PDF page.
"""
from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

try:
    import pymupdf
except ImportError:          # older PyMuPDF installs expose it as fitz
    import fitz as pymupdf

HERE = Path(__file__).resolve().parent
ANALYSIS_DIR = HERE / "analysis-result"
PDF_DIR = HERE / "test-books"
OUTPUT_DIR = HERE / "output"
OVERRIDES_DIR = HERE / "book-overrides"

_SPACES = re.compile(r"\s+")
_SUSPICIOUS = re.compile("[\ufffd\ue000-\uf8ff]")   # replacement char, private-use area
# Symbol-font glyphs arrive as U+F0xx, where xx is the code in Adobe's Symbol font encoding.
SYMBOL_PUA = str.maketrans({
    0xF028: "(", 0xF029: ")", 0xF02B: "+", 0xF03D: "=", 0xF0B4: "×",
    0xF0E9: "[", 0xF0EB: None,      # top / bottom piece of a large left bracket
    0xF0F9: "]", 0xF0FB: None,      # top / bottom piece of a large right bracket
})


# ----------------------------------------------------------------------------- rules
@dataclass
class Rules:
    header_bottom: float | None      # drop lines whose bottom edge is at or above this y
    footer_top: float | None         # drop lines whose top edge is at or below this y
    gutter_x: float | None           # x of the gap between two columns
    min_column_lines: int            # lines needed on EACH side to call a page two-column
    strip_chars: str                 # invisible characters to delete
    scanned_pages: frozenset[int]    # 1-based pages with no text layer (need OCR)
    expected_two_column_pages: int | None

    @classmethod
    def from_profile(cls, profile: dict, overrides: dict) -> "Rules":
        recipe = profile["recipe"]
        hf, layout, enc = recipe["header_footer"], recipe["layout"], recipe["encoding_fonts"]
        merged = {
            "header_band_bottom_pt": hf.get("header_band_bottom_pt"),
            "footer_band_top_pt": hf.get("footer_band_top_pt"),
            "gutter_x_pt": layout.get("gutter_x_pt"),
            "min_column_lines": 8,     # the analyser's rule: >= 8 lines on each side of the gutter
        }
        merged.update(overrides)       # human corrections win over the analyser
        return cls(
            header_bottom=merged["header_band_bottom_pt"],
            footer_top=merged["footer_band_top_pt"],
            gutter_x=merged["gutter_x_pt"],
            min_column_lines=merged["min_column_lines"],
            strip_chars="".join(chr(int(c[2:], 16)) for c in enc.get("strip_chars", [])),
            scanned_pages=frozenset(recipe["overview"].get("scanned_pages", [])),
            expected_two_column_pages=layout.get("two_column_pages"),
        )


# ----------------------------------------------------------------------------- text
def clean_text(text: str, strip_table: dict) -> str:
    text = unicodedata.normalize("NFKC", text)     # also expands ligatures like fi, fl
    text = text.translate(SYMBOL_PUA)              # symbol-font punctuation and brackets
    text = text.translate(strip_table)             # soft hyphens, zero-width characters
    return _SPACES.sub(" ", text).strip()


def style_key(span: dict) -> str:
    """Same format as the profile: 'IBMPlexSans-Bold 9pt bold #000000'."""
    size = f"{span['size']:.1f}".rstrip("0").rstrip(".")
    bold = " bold" if span["flags"] & 16 else ""
    return f"{span['font']} {size}pt{bold} #{span['color']:06x}"


def norm_style(style: str) -> str:
    """The profile sometimes drops spaces ('Mulish-Black10.8pt bold #b10069'): compare without them."""
    return re.sub(r"\s+", "", style)


# ----------------------------------------------------------------------------- one page
def read_lines(page, strip_table: dict) -> list[dict]:
    lines = []
    data = page.get_text("dict", flags=pymupdf.TEXT_MEDIABOX_CLIP)   # no images, ligatures expanded
    for block in data["blocks"]:
        if block["type"] != 0:
            continue
        for ln in block["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            text = clean_text("".join(s["text"] for s in ln["spans"]), strip_table)
            if not spans or not text:
                continue
            if "\ufffd" in text and not text.replace("\ufffd", "").strip():
                continue   # a line of nothing but unreadable glyphs carries no information
            styles = Counter()
            for s in spans:
                styles[style_key(s)] += len(s["text"])
            x0, y0, x1, y1 = ln["bbox"]
            dx, dy = ln["dir"]
            lines.append({"t": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "col": 0,
                          "styles": styles, "rot": not (dx > 0.9 and abs(dy) < 0.1)})
    return lines


def split_furniture(lines: list[dict], rules: Rules):
    body, header, footer = [], 0, 0
    for l in lines:
        if rules.header_bottom is not None and l["y1"] <= rules.header_bottom:
            header += 1
        elif rules.footer_top is not None and l["y0"] >= rules.footer_top:
            footer += 1
        else:
            body.append(l)
    return body, header, footer


MIN_ZONE_LINES = 2   # lines needed on EACH side of the gutter, within one block, to read it as two columns


def tag_columns(lines: list[dict], rules: Rules) -> None:
    """col 0 = crosses the gutter (full width), 1 = left of it, 2 = right of it."""
    g = rules.gutter_x
    for l in lines:
        if g is None:
            l["col"] = 0
        elif l["x1"] <= g + 2:
            l["col"] = 1
        elif l["x0"] >= g - 2:
            l["col"] = 2
        else:
            l["col"] = 0


def _adjacent(a: dict, b: dict) -> bool:
    """True when a and b are pieces of the same visual line (same row, touching horizontally)."""
    ha, hb = a["y1"] - a["y0"], b["y1"] - b["y0"]
    shorter = min(ha, hb)
    overlap = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    gap = max(b["x0"] - a["x1"], a["x0"] - b["x1"])
    return shorter > 0 and overlap >= 0.3 * shorter and -2 <= gap <= max(ha, hb)


def merge_fragments(lines: list[dict]) -> list[dict]:
    """Superscripts and inline symbols make PyMuPDF split one line into several: glue them back."""
    groups: list[dict] = []
    for l in sorted(lines, key=lambda l: (l["col"], l["y0"], l["x0"])):
        for g in reversed(groups[-8:]):
            if g["col"] == l["col"] and _adjacent(g, l):
                g["parts"].append(l)
                g["x0"], g["y0"] = min(g["x0"], l["x0"]), min(g["y0"], l["y0"])
                g["x1"], g["y1"] = max(g["x1"], l["x1"]), max(g["y1"], l["y1"])
                g["styles"].update(l["styles"])
                break
        else:
            groups.append({**l, "parts": [l], "styles": Counter(l["styles"])})
    return [_finish(g) for g in groups]


def _finish(g: dict) -> dict:
    parts = sorted(g["parts"], key=lambda p: p["x0"])
    text = parts[0]["t"]
    for prev, p in zip(parts, parts[1:]):
        gap = p["x0"] - prev["x1"]
        text += ("" if gap < 0.15 * (prev["y1"] - prev["y0"]) else " ") + p["t"]
    return {**g, "t": text}


def reading_order(lines: list[dict]):
    """Read the page in blocks. Full-width lines (headings, captions, tables) divide it into blocks;
    inside a block with text on both sides of the gutter, read the whole left column, then the right.

    Returns (ordered lines, number of lines read as left column, number read as right column).
    """
    ordered, left, right = [], [], []
    columnar = [0, 0]

    def flush():
        if len(left) >= MIN_ZONE_LINES and len(right) >= MIN_ZONE_LINES:
            ordered.extend(sorted(left, key=lambda l: l["y0"]))
            ordered.extend(sorted(right, key=lambda l: l["y0"]))
            columnar[0] += len(left)
            columnar[1] += len(right)
        else:                                  # not columns: read top to bottom
            ordered.extend(sorted(left + right, key=lambda l: (l["y0"], l["x0"])))
        left.clear()
        right.clear()

    for l in sorted(lines, key=lambda l: (l["y0"], l["x0"])):
        if l["col"] == 0:
            flush()
            ordered.append(l)
        elif l["col"] == 1:
            left.append(l)
        else:
            right.append(l)
    flush()
    return ordered, columnar[0], columnar[1]


def to_output(l: dict) -> dict:
    out = {"t": l["t"], "x0": round(l["x0"], 1), "y0": round(l["y0"], 1),
           "x1": round(l["x1"], 1), "y1": round(l["y1"], 1), "col": l["col"],
           "style": l["styles"].most_common(1)[0][0]}   # DOMINANT style, not the first span's
    if l["rot"]:
        out["rot"] = True
    return out


def process_page(page, rules: Rules, strip_table: dict) -> dict:
    pdf_page = page.number + 1                       # 1-based everywhere, like the page map
    record = {"pdf_page": pdf_page, "cols": 1, "needs_ocr": False,
              "dropped": {"header": 0, "footer": 0}, "lines": []}
    if pdf_page in rules.scanned_pages:
        record["needs_ocr"] = True                   # OCR is a later, separate stage
        return record

    body, record["dropped"]["header"], record["dropped"]["footer"] = split_furniture(
        read_lines(page, strip_table), rules)
    upright = [l for l in body if not l["rot"]]
    rotated = [l for l in body if l["rot"]]
    tag_columns(upright, rules)
    ordered, n_left, n_right = reading_order(merge_fragments(upright))
    record["cols"] = 2 if min(n_left, n_right) >= rules.min_column_lines else 1
    ordered += sorted(rotated, key=lambda l: (l["x0"], l["y0"]))   # rotated text: tables, later
    record["lines"] = [to_output(l) for l in ordered]
    return record


# ----------------------------------------------------------------------------- running
def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_pdf(book_name: str) -> Path:
    for pdf in PDF_DIR.glob("*.pdf"):
        if _norm_name(pdf.stem) == _norm_name(book_name):
            return pdf
    raise FileNotFoundError(f"No PDF in {PDF_DIR} matching '{book_name}'")


def parse_pages(spec: str | None, total: int) -> tuple[int, int]:
    if not spec:
        return 1, total
    a, _, b = spec.partition("-")
    return max(1, int(a)), min(total, int(b or a))


def dump(record: dict, show_styles: bool) -> None:
    print(f"\n--- pdf page {record['pdf_page']}  columns={record['cols']}  dropped={record['dropped']}"
          f"{'  NEEDS OCR' if record['needs_ocr'] else ''}")
    for l in record["lines"]:
        extra = f"   <{l['style']}>" if show_styles else ""
        print(f"[{l['col']}] y{l['y0']:6.1f} x{l['x0']:5.1f}  {l['t'][:100]}{extra}")


def run_book(profile_path: Path, pages_spec: str | None, do_dump: bool, show_styles: bool) -> None:
    name = profile_path.parent.name
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    override_file = OVERRIDES_DIR / f"{name}.json"
    overrides = json.loads(override_file.read_text(encoding="utf-8")) if override_file.exists() else {}
    rules = Rules.from_profile(profile, overrides)
    strip_table = str.maketrans("", "", rules.strip_chars)

    with pymupdf.open(find_pdf(name)) as doc:
        first, last = parse_pages(pages_spec, doc.page_count)
        full_run = first == 1 and last == doc.page_count
        out_dir = OUTPUT_DIR / name
        out_path = out_dir / ("pages.jsonl" if full_run else f"pages_{first}-{last}.jsonl")
        out = None
        if not do_dump:
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_path.open("w", encoding="utf-8")

        stats = Counter()
        started = time.time()
        for n in range(first, last + 1):
            record = process_page(doc[n - 1], rules, strip_table)
            if do_dump:
                dump(record, show_styles)
                continue
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            stats["pages"] += 1
            stats["lines"] += len(record["lines"])
            stats["two_column_pages"] += record["cols"] == 2
            stats["header_lines_dropped"] += record["dropped"]["header"]
            stats["footer_lines_dropped"] += record["dropped"]["footer"]
            stats["pages_needing_ocr"] += record["needs_ocr"]
            stats["pages_without_text"] += (not record["lines"]) and not record["needs_ocr"]
            stats["rotated_lines"] += sum(1 for l in record["lines"] if l.get("rot"))
            stats["suspicious_chars"] += sum(len(_SUSPICIOUS.findall(l["t"])) for l in record["lines"])
            if n % 200 == 0:
                print(f"  [{name}] page {n}/{last}  ({time.time() - started:.0f}s)")
        if out:
            out.close()

    if do_dump:
        return
    print("=" * 80)
    print("BOOK:", name, "->", out_path)
    print(f"rules: header_bottom={rules.header_bottom} footer_top={rules.footer_top} "
          f"gutter_x={rules.gutter_x} scanned_pages={sorted(rules.scanned_pages)}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    if full_run and rules.expected_two_column_pages is not None:
        print(f"  two-column pages: {stats['two_column_pages']}  (profile says {rules.expected_two_column_pages})")
    print(f"  time: {time.time() - started:.0f}s\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Step 3: extract clean, reading-ordered lines per page")
    ap.add_argument("--book", help="part of the book folder name (default: every book)")
    ap.add_argument("--pages", help="PDF pages, 1-based: '23' or '17-60' (default: all)")
    ap.add_argument("--dump", action="store_true", help="print the pages instead of writing a file")
    ap.add_argument("--styles", action="store_true", help="with --dump, show each line's style")
    args = ap.parse_args()
    if args.dump and not args.pages:
        ap.error("--dump needs --pages (do not print a whole book)")

    profiles = sorted(p for p in ANALYSIS_DIR.glob("*/profile.json")
                      if not args.book or args.book.lower() in p.parent.name.lower())
    if not profiles:
        raise SystemExit(f"No profile.json under {ANALYSIS_DIR} matching '{args.book}'")
    for profile_path in profiles:
        run_book(profile_path, args.pages, args.dump, args.styles)


if __name__ == "__main__":
    main()
