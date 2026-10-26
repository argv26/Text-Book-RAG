from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path


class TreeError(ValueError):
    """The outline is structurally broken: stop, don't guess."""


@dataclass
class Section:
    id: int                      # the analyser's entry index
    title: str
    level: int
    kind: str                    # numbered_section, chapter, key_terms, ...
    use: str                     # teaching | container | skip | review | ...
    parent_id: int | None
    pdf_start: int               # 1-based, inclusive
    pdf_end: int                 # 1-based, inclusive (may be shared with the next section)
    heading_path: str = ""
    children: list[int] = field(default_factory=list)
    printed_start: int | None = None
    printed_end: int | None = None
    boundary_source: str = "outline"

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["is_leaf"] = self.is_leaf
        return d


def load_entries(profile_path: Path) -> list[dict]:
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return profile["analyzers"]["outline"]["facts"]["entries_list"]


def _printed(page_map, pdf_page: int) -> int | None:
    if page_map is None:
        return None
    try:
        return page_map.pdf_to_printed(pdf_page)
    except ValueError:
        return None


def build_tree(entries: list[dict], page_map=None) -> tuple[list[Section], dict[str, list[str]]]:
    """Returns (sections in document order, issues grouped by category)."""
    issues: dict[str, list[str]] = defaultdict(list)
    sections: list[Section] = []

    for pos, e in enumerate(entries):
        # Hard errors: the tree cannot be trusted if these fail.
        if e["index"] != pos:
            raise TreeError(f"entry at position {pos} has index {e['index']}")
        parent = e["parent"]
        if parent is not None and not (0 <= parent < pos):
            raise TreeError(f"entry {pos} has parent {parent}, which does not come before it")

        start, end = e["page"], e["next_start_page"]
        if end < start:
            issues["end_before_start"].append(f"{pos} '{e['title']}': {start}..{end}")
            end = start

        s = Section(
            id=pos, title=e["title"].strip(), level=e["level"], kind=e["kind"],
            use=e["use"], parent_id=parent, pdf_start=start, pdf_end=end,
            printed_start=_printed(page_map, start), printed_end=_printed(page_map, end),
        )
        if parent is None:
            s.heading_path = s.title
        else:
            p = sections[parent]
            p.children.append(pos)
            s.heading_path = f"{p.heading_path} > {s.title}"
            if s.level <= p.level:
                issues["level_not_deeper"].append(f"{pos} '{s.title}' (L{s.level}) under '{p.title}' (L{p.level})")
            if s.pdf_start < p.pdf_start or s.pdf_end > p.pdf_end:
                issues["child_outside_parent"].append(
                    f"{pos} '{s.title}' {s.pdf_start}..{s.pdf_end} vs parent {p.pdf_start}..{p.pdf_end}")
        sections.append(s)

    # Soft checks: report, don't crash.
    for s in sections:
        starts = [sections[c].pdf_start for c in s.children]
        if starts != sorted(starts):
            issues["sibling_order"].append(f"children of {s.id} '{s.title}' are not in page order")

    roots = [s for s in sections if s.parent_id is None]
    for a, b in zip(roots, roots[1:]):
        if a.pdf_end != b.pdf_start:
            issues["roots_do_not_chain"].append(f"'{a.title}' ends {a.pdf_end}, next '{b.title}' starts {b.pdf_start}")

    return sections, issues


def summarise(name: str, sections: list[Section], issues: dict[str, list[str]]) -> None:
    leaves = [s for s in sections if s.is_leaf]
    print("=" * 80)
    print("BOOK:", name)
    print(f"sections: {len(sections)}  roots: {sum(s.parent_id is None for s in sections)}  "
          f"leaves: {len(leaves)}  max level: {max(s.level for s in sections)}")
    print("use:", dict(Counter(s.use for s in sections)))
    print("leaf kinds:", dict(Counter(s.kind for s in leaves).most_common(8)))

    print("\nwidest leaves by page span (hints for the chunking step):")
    for s in sorted(leaves, key=lambda s: s.pdf_end - s.pdf_start, reverse=True)[:3]:
        print(f"  {s.pdf_end - s.pdf_start + 1:>4} pages  p{s.pdf_start}  {s.heading_path[:90]}")

    print("\nissues:", "none" if not issues else "")
    for category, items in issues.items():
        print(f"  {category}: {len(items)}")
        for item in items[:3]:
            print("     ", item)

    print("\npreview (first root with children, depth <= 3):")
    shown = 0
    stack = [next(s for s in sections if s.parent_id is None and s.children)]
    while stack and shown < 20:
        s = stack.pop(0)
        depth = s.level - stack_base(sections, s)
        print(f"  {'  ' * depth}{s.title[:60]}  [p{s.pdf_start}-{s.pdf_end}, printed {s.printed_start}]"
              f"{'' if s.children else '  (leaf)'}")
        shown += 1
        if depth < 2:
            stack = [sections[c] for c in s.children] + stack
    print()


def stack_base(sections: list[Section], s: Section) -> int:
    # Level of the root of s's tree, so the preview indents relative to it.
    while s.parent_id is not None:
        s = sections[s.parent_id]
    return s.level


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    root = here / "analysis-result"
    out_root = here / "output"

    profiles = sorted(root.glob("*/profile.json"))
    if not profiles:
        raise SystemExit(f"No profile.json files under {root}")

    try:
        from page_map import PageMap   # your step 1
    except ImportError:
        PageMap = None

    for profile_path in profiles:
        name = profile_path.parent.name
        page_map = None
        if PageMap is not None:
            try:
                page_map = PageMap.from_profile(profile_path)
            except Exception as exc:   # step 1 may not support this book's layout yet
                print(f"[{name}] page map unavailable: {exc!r}")

        sections, issues = build_tree(load_entries(profile_path), page_map)
        summarise(name, sections, issues)

        out_dir = out_root / name
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "sections.json").write_text(
            json.dumps([s.to_dict() for s in sections], indent=1, ensure_ascii=False), encoding="utf-8")