"""Sanitizers and text post-processing for medical typography, citations, and OCR cleanup."""
from __future__ import annotations

import re


def _clean_chunk_prose(text: str) -> str:
    """Cleans OCR artifacts, hyphenated line wraps, reference indices, and diagram fragments."""
    if not text:
        return ""
    # 1. Hyphenated word wrap
    text = re.sub(r"(\b\w+)-\s*\n\s*(\w+\b)", r"\1\2", text)
    # 2. Known broken suffixes wrapped across lines without hyphen
    broken_suffixes = (
        r"(?:ates|tant|poral|tion|tions|sis|ment|ments|ular|ence|ency|able|ology|"
        r"genic|genous|ically|ary|ive|ity|ties|ism|isms|oids?)"
    )
    text = re.sub(rf"(\b[a-zA-Z]{{3,}})\s*\n\s*({broken_suffixes}\b)", r"\1\2", text, flags=re.IGNORECASE)
    # 3. Convert remaining newlines to single spaces
    text = re.sub(r"[\r\n]+", " ", text)
    # 4. Remove footnote numbers and references like .13,14 or ,15,16 or SF1.15,16
    text = re.sub(r"(?<=[a-zA-Z0-9])\.\d{1,3}(?:,\d{1,3})+(?=\s|$)", ".", text)
    text = re.sub(r"(?<=[a-zA-Z])\.\d{1,3}(?=\s|$)", ".", text)
    text = re.sub(r"(?<=[a-zA-Z]),\d{1,3}(?:,\d{1,3})*(?=\s|$)", ",", text)
    # 5. Remove diagram labels and OCR noise
    text = re.sub(r"[•·\ufffd]", " ", text)
    text = re.sub(r"\bFIGURE?\s*\d+(\.\d+)*\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTABLE\s*\d+(\.\d+)*\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(Fig\.[^\)]*\)", "", text, flags=re.IGNORECASE)
    # 6. Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_coherent_sentences(text: str, max_sentences: int = 3) -> str:
    """Extracts coherent, readable sentences, filtering out OCR tables and diagram labels."""
    cleaned = _clean_chunk_prose(text)
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if len(s.strip()) > 15]
    coherent: list[str] = []
    for s in raw_sentences:
        words = s.split()
        if len(words) < 5:
            continue
        if any(term in s.lower() for term in ["micrograph provided by", "courtesy of", "reproduced with permission"]):
            continue
        coherent.append(s)
        if len(coherent) >= max_sentences:
            break
    return " ".join(coherent)


def _sanitize_medical_typography(text: str) -> str:
    """Sanitizes raw LaTeX artifacts, math mode delimiters, and enforces clean medical typography."""
    if not text:
        return ""

    # 1. Strip \text{...} LaTeX markup: e.g. \text{pH} -> pH, \text{ mmol/L} ->  mmol/L
    text = re.sub(r"\\text\{([^}]+)\}", r"\1", text)

    # 2. Normalize thyroid and clinical hormones: $T_4$ -> T4, $T_3$ -> T3, $fT_4$ -> fT4
    text = re.sub(r"\$?\b([fF]?[tT])_([34])\b\$?", r"\1\2", text)
    text = re.sub(r"\$([tT][34])\$", r"\1", text)

    # 3. Convert common LaTeX mathematical symbols to clean Unicode
    latex_syms = [
        (r"\\(?:ge|geq)\b", "≥"),
        (r"\\(?:le|leq)\b", "≤"),
        (r"\\sim\b", "~"),
        (r"\\pm\b", "±"),
        (r"\\times\b", "×"),
        (r"\\(?:mu|micro)\b", "μ"),
        (r"\\alpha\b", "α"),
        (r"\\beta\b", "β"),
        (r"\\gamma\b", "γ"),
        (r"\\(?:Delta|delta)\b", "Δ"),
        (r"\\(?:degree|circ)\b", "°"),
    ]
    for pattern, replacement in latex_syms:
        text = re.sub(pattern, replacement, text)

    # 4. Unwrap single dollar math delimiters wrapping clinical units, expressions, or receptors
    # e.g. $pH < 7.3$, $< 15 mmol/L$, $200–250 mg/dL$, $TR\beta2$ -> $TRβ2$ -> TRβ2
    text = re.sub(r"\$([A-Za-z0-9\s\.\<\>\=\-\+~±×μ\u0370-\u03ff°%/–—\(\)]+)\$", r"\1", text)

    # 5. Clean up any leftover empty brackets or redundant whitespace
    text = re.sub(r"\(\s*\)", "", text)
    return text


def _sanitize_citations(reply: str, valid_citation_ids: set[int]) -> str:
    """Ensures all [N] citation pills correspond to registered citations in the session.
    Out-of-range citations (e.g. LLM copying textbook bibliography numbers like [14])
    are converted to plaintext '(Ref 14)' so un-cached clickable pills are never emitted.
    """
    if not reply:
        return ""

    def _replace_cit(match: re.Match) -> str:
        num_str = match.group(1)
        try:
            num = int(num_str)
            if num in valid_citation_ids:
                return f"[{num}]"
            else:
                return f"(Ref {num})"
        except ValueError:
            return match.group(0)

    return re.sub(r"\[(\d+)\]", _replace_cit, reply)


# Public aliases
clean_chunk_prose = _clean_chunk_prose
extract_coherent_sentences = _extract_coherent_sentences
sanitize_medical_typography = _sanitize_medical_typography
sanitize_citations = _sanitize_citations

