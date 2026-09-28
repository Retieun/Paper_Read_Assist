"""Citation labels that follow the paper's bibliography style, and the References block."""
from __future__ import annotations

import html as htmllib
import re
from typing import Optional

from .bib import BibEntry

ALPHA_STYLES = {"alpha", "amsalpha", "alphaurl", "alphabetic"}
UNSORTED_STYLES = {"unsrt", "unsrtnat", "ieeetr", "ieeetran", "numeric-comp", "numeric"}  # numeric biblatex sorts by default; treat as unsorted only when sorting=none
AUTHORYEAR_STYLES = {"authoryear", "authoryear-comp", "apa", "chicago-authordate", "plainnat", "abbrvnat", "natbib", "apalike", "apacite", "chicago", "harvard", "agsm", "dcu", "kluwer"}


def _last_names(entry: BibEntry) -> list[str]:
    from .bib import _last_name

    return [_last_name(a) for a in entry.authors if a.strip()]


def alpha_label(entry: BibEntry) -> str:
    names = _last_names(entry)
    yy = (entry.year or "")[-2:]
    if not names:
        return (entry.key[:3] + yy) or entry.key
    if len(names) == 1:
        core = re.sub(r"[^A-Za-z]", "", names[0])[:3]
    elif len(names) <= 4:
        core = "".join(re.sub(r"[^A-Za-z]", "", n)[:1] for n in names)
    else:
        core = "".join(re.sub(r"[^A-Za-z]", "", n)[:1] for n in names[:3]) + "+"
    return core + yy


def _sort_key(entry: Optional[BibEntry], key: str):
    if entry is None:
        return ("~", "", key)
    names = " ".join(n.lower() for n in _last_names(entry)) or entry.key.lower()
    return (names, entry.year or "", entry.title.lower())


def label_mode(bib_style: Optional[str], biblatex_style: Optional[str]) -> str:
    """'alpha' | 'unsorted' | 'sorted' | 'authoryear'."""
    st = (biblatex_style or bib_style or "plain").lower()
    if st in ALPHA_STYLES:
        return "alpha"
    if st in AUTHORYEAR_STYLES:
        return "authoryear"
    if st in UNSORTED_STYLES:
        return "unsorted"
    return "sorted"


def compute_labels(cited_in_order: list[str], bib: dict[str, BibEntry], mode: str) -> tuple[dict[str, str], list[str]]:
    """Return (key -> label, keys in bibliography order)."""
    keys = list(dict.fromkeys(cited_in_order))
    if mode == "authoryear":
        labels = {k: (bib[k].short if k in bib else k) for k in keys}
        order = sorted(keys, key=lambda k: _sort_key(bib.get(k), k))
        return labels, order
    if mode == "alpha":
        order = sorted(keys, key=lambda k: _sort_key(bib.get(k), k))
        labels: dict[str, str] = {}
        seen: dict[str, int] = {}
        for k in order:
            base = alpha_label(bib[k]) if k in bib else k[:6]
            n = seen.get(base, 0)
            seen[base] = n + 1
            labels[k] = base
        # disambiguate duplicates with a, b, c
        counts = {}
        for k in order:
            counts[labels[k]] = counts.get(labels[k], 0) + 1
        suffix: dict[str, int] = {}
        for k in order:
            base = labels[k]
            if counts[base] > 1:
                i = suffix.get(base, 0)
                labels[k] = base + "abcdefghijklmnopqrstuvwxyz"[i % 26]
                suffix[base] = i + 1
        return labels, order
    if mode == "unsorted" or not any(k in bib for k in keys):
        order = keys
    else:
        known = sorted([k for k in keys if k in bib], key=lambda k: _sort_key(bib.get(k), k))
        unknown = [k for k in keys if k not in bib]
        order = known + unknown
    labels = {k: str(i + 1) for i, k in enumerate(order)}
    return labels, order


def bib_anchor(key: str) -> str:
    return "lbl-bib-" + re.sub(r"[^A-Za-z0-9:_.\-]", "_", key)


def format_entry_html(entry: Optional[BibEntry], key: str) -> str:
    e = htmllib.escape
    if entry is None:
        return f"<span class=\"pa-bib-missing\">{e(key)} (not in the bibliography file)</span>"
    parts = []
    if entry.authors:
        parts.append(e(", ".join(entry.authors)) + ".")
    if entry.title:
        parts.append(f"<em>{e(entry.title)}</em>.")
    venue = entry.venue
    vol = entry.fields.get("volume")
    pages = entry.fields.get("pages")
    tail = venue
    if vol:
        tail += f" {vol}"
    if pages and not pages.lower().startswith(("doi", "http")):
        tail += f", {pages}"
    if entry.year:
        tail += f" ({entry.year})"
    if tail.strip():
        parts.append(e(tail.strip()) + ".")
    links = []
    if entry.arxiv:
        links.append(f"<a class=\"pa-extlink\" href=\"https://arxiv.org/abs/{e(entry.arxiv)}\" target=\"_blank\" rel=\"noopener\">arXiv:{e(entry.arxiv)}</a>")
    if entry.doi:
        links.append(f"<a class=\"pa-extlink\" href=\"https://doi.org/{e(entry.doi)}\" target=\"_blank\" rel=\"noopener\">doi</a>")
    elif entry.url:
        links.append(f"<a class=\"pa-extlink\" href=\"{e(entry.url)}\" target=\"_blank\" rel=\"noopener\">link</a>")
    html = " ".join(parts)
    if links:
        html += " <span class=\"pa-bib-links\">" + " · ".join(links) + "</span>"
    return html


def format_entry_text(entry: Optional[BibEntry], key: str) -> str:
    if entry is None:
        return key
    bits = [", ".join(entry.authors), entry.title, entry.venue, entry.year or ""]
    return ". ".join(b for b in bits if b)


def bibliography_block_html(order: list[str], labels: dict[str, str], bib: dict[str, BibEntry]) -> tuple[str, str]:
    e = htmllib.escape
    rows = []
    lines = ["References"]
    for k in order:
        entry = bib.get(k)
        arx = f" data-arxiv=\"{e(entry.arxiv)}\"" if entry and entry.arxiv else ""
        rows.append(
            f"<div class=\"pa-bib-entry\" id=\"{bib_anchor(k)}\" data-key=\"{e(k)}\"{arx}>"
            f"<span class=\"pa-bib-label\">[{e(labels[k])}]</span> <span class=\"pa-bib-text\">{format_entry_html(entry, k)}</span></div>"
        )
        lines.append(f"[{labels[k]}] {format_entry_text(entry, k)} (key: {k})")
    html = "<h2 class=\"pa-heading pa-h1\">References</h2><div class=\"pa-bib\">" + "".join(rows) + "</div>"
    return html, "\n".join(lines)
