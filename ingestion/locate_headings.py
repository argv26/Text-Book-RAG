"""Step 4a: find where each outline section starts, as a line position in the page's reading order.

Input : output/<book>/sections.json (step 2), output/<book>/pages.jsonl (step 3), profile.json
Output: output/<book>/headings.json, one entry per section (found or not, and where)
"""
from __future__ import annotations

import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from extract_pages import norm_style

HERE = Path(__file__).resolve().parent
ANALYSIS_DIR = HERE / "analysis-result"
OUTPUT_DIR = HERE / "output"

MAX_LINES = 3        # a heading may wrap onto up to this many lines
FUZZY_MIN = 0.92     # similarity needed when the text is not an exact match
PREFIX_MIN = 25      # a title this long may be matched as the START of longer text (run-in heading + first words)


def normalise(text: str) -> str:
    """Casefold and keep only letters and digits, so punctuation and spacing differences do not matter."""
    import unicodedata
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def _joinable(a: dict, b: dict) -> bool:
    """Consecutive lines can form one heading if they share a column or either one spans the page."""
    return a["col"] == b["col"] or 0 in (a["col"], b["col"])


def find_on_page(title: str, lines: list[dict], body_style: str):
    """Best run of 1..MAX_LINES consecutive lines that reads as `title`.

    Returns (is_heading_style, kind, start_index, end_index) or None, where kind is
    'exact', 'prefix' (the title is the start of the run's text) or 'fuzzy'.
    Prefers a run whose style differs from the body text, then the better match, then the earlier one.
    """
    target = normalise(title)
    if len(target) < 2:
        return None
    letters = "".join(ch for ch in title if ch.isalnum())
    title_caps = letters.upper()
    best = None
    for i, first in enumerate(lines):
        if first.get("rot"):
            continue
        # A SHORT title is trusted as the start of a longer line only when the page spells it in CAPITALS
        # and the outline does not: a run-in label such as 'INTRODUCTION Though you may approach ...'.
        caps_label = (title_caps != letters and
                      "".join(ch for ch in first["t"] if ch.isalnum()).startswith(title_caps))
        joined = ""
        for j in range(i, min(i + MAX_LINES, len(lines))):
            line = lines[j]
            if line.get("rot") or (j > i and not _joinable(lines[j - 1], line)):
                break
            joined += line["t"]
            cand = normalise(joined)
            if not cand:
                break
            if cand == target:
                kind, score = "exact", 1.0
            elif cand.startswith(target) and (len(target) >= PREFIX_MIN or caps_label):
                kind, score = "prefix", 0.99
            elif len(cand) > len(target) * 1.3:
                break
            else:
                kind, score = "fuzzy", SequenceMatcher(None, cand, target).ratio()
                if score < FUZZY_MIN:
                    continue
            heading_style = any(norm_style(l["style"]) != body_style for l in lines[i:j + 1])
            key = (heading_style, score, -i)
            if best is None or key > best[0]:
                best = (key, kind, i, j)
    if best is None:
        return None
    (heading_style, _, _), kind, i, j = best
    return heading_style, kind, i, j


def locate(sections: list[dict], pages: dict[int, list[dict]], body_style: str) -> list[dict]:
    results = []
    for s in sections:
        entry = {"id": s["id"], "title": s["title"], "found": False, "page": None,
                 "start": None, "end": None, "mode": None}
        # The outline's own page first, then the neighbours (the outline can be off by one).
        for offset, mode in ((0, "own_page"), (1, "next_page"), (-1, "previous_page")):
            page = s["pdf_start"] + offset
            hit = find_on_page(s["title"], pages.get(page, []), body_style)
            if hit:
                heading_style, kind, i, j = hit
                entry.update(found=True, page=page, start=i, end=j,
                             mode=f"{mode}/{kind}" + ("" if heading_style else "/body_style"))
                break
        results.append(entry)
    return results


def run_book(profile_path: Path) -> None:
    name = profile_path.parent.name
    out_dir = OUTPUT_DIR / name
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    sections = json.loads((out_dir / "sections.json").read_text(encoding="utf-8"))
    pages = {}
    with (out_dir / "pages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            pages[r["pdf_page"]] = r["lines"]
    body_style = norm_style(profile["recipe"]["typography"]["body_style"])

    results = locate(sections, pages, body_style)
    (out_dir / "headings.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    found = sum(r["found"] for r in results)
    expected = profile["recipe"]["heading_locator"].get("found_rate")
    print("=" * 80)
    print("BOOK:", name)
    print(f"sections: {len(results)}   located: {found} ({found / len(results):.1%})"
          f"   profile's own rate: {expected}")
    print("how they were found:", dict(Counter(r["mode"] for r in results if r["found"])))
    by_id = {s["id"]: s for s in sections}
    missing = [r for r in results if not r["found"]]
    print("not located, by kind:", dict(Counter(by_id[r["id"]]["kind"] for r in missing).most_common(8)))
    print("first not located:")
    for r in missing[:12]:
        print(f"   p{by_id[r['id']]['pdf_start']}  [{by_id[r['id']]['kind']}]  {r['title'][:70]}")
    print()


if __name__ == "__main__":
    for profile_path in sorted(ANALYSIS_DIR.glob("*/profile.json")):
        run_book(profile_path)