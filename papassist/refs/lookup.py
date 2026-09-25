"""Searching cited papers for a definition, and showing their blocks in this paper's reader."""
from __future__ import annotations

import re
from typing import Optional

from ..glossary.textutil import canonical_term, term_variants
from ..ingest.document import Block, Document

POINTER_KINDS = {
    "thm": "theorem", "theorem": "theorem", "theorems": "theorem", "thms": "theorem",
    "lem": "lemma", "lemma": "lemma", "prop": "proposition", "proposition": "proposition",
    "cor": "corollary", "corollary": "corollary", "def": "definition", "defn": "definition", "definition": "definition",
    "rem": "remark", "remark": "remark", "ex": "example", "example": "example", "conj": "conjecture",
    "sec": "section", "section": "section", "§": "section", "ch": "chapter", "chapter": "chapter",
    "eq": "equation", "equation": "equation", "prob": "problem", "claim": "claim", "fact": "fact",
    "constr": "construction", "construction": "construction", "alg": "construction", "algorithm": "construction",
    "p": "page", "pp": "page", "page": "page", "pages": "page",
}
POINTER_RE = re.compile(r"(§+|[A-Za-z]{1,12}\.?)\s*~?\s*([0-9]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)+|[IVX]+(?:\.[0-9]+)*)", re.U)


def parse_pointer(suffix: str) -> list[dict]:
    """'Thm.~4.4' -> [{'kind': 'theorem', 'number': '4.4'}]; '§3.2, Def. 2.1' -> two pointers."""
    out = []
    s = suffix.replace(" ", " ").replace("~", " ")
    for m in POINTER_RE.finditer(s):
        word = m.group(1).rstrip(".").lower()
        kind = POINTER_KINDS.get(word) or ("section" if word.startswith("§") else None)
        if kind is None:
            continue
        out.append({"kind": kind, "number": m.group(2)})
    return out


def find_block_by_pointer(doc: Document, pointer: dict) -> Optional[Block]:
    kind, number = pointer["kind"], pointer["number"]
    if kind == "section":
        for b in doc.blocks:
            if b.kind == "heading" and b.number == number:
                return b
        return None
    if kind == "equation":
        for lbl, info in doc.labels.items():
            if info.get("kind") == "equation" and info.get("number") == number and info.get("block"):
                return doc.block_by_id(info["block"])
        return None
    if kind == "page":
        return None
    for b in doc.blocks:
        if b.kind == "theorem" and b.number == number and (b.thm_kind == kind or kind == "theorem"):
            return b
    # numbering may be shared: accept any theorem-like block with that number
    for b in doc.blocks:
        if b.kind == "theorem" and b.number == number:
            return b
    return None


def _term_regex(key: str, aliases: list[str]) -> re.Pattern:
    forms = set()
    for f in [key, *aliases]:
        forms.update(term_variants(f))
    forms = sorted((f for f in forms if len(f) >= 3), key=len, reverse=True)
    return re.compile(r"(?<![\w\-])(" + "|".join(re.escape(f).replace(r"\ ", r"[\s\-\u2013\u2014]+") for f in forms) + r")(?![\w\-])", re.I)


def lookup_term(doc: Document, glossary, term: str, pointer_blocks: Optional[list[Block]] = None) -> list[dict]:
    """Definitions of ``term`` in another paper: glossary entries first, then definition-like blocks mentioning it."""
    key = canonical_term(term) or term.lower().strip()
    hits: list[dict] = []
    seen: set[str] = set()
    for t in glossary.term(key):
        if t.get("status") == "undefined":
            continue
        bid = t.get("definition_block")
        if bid and bid not in seen:
            seen.add(bid)
            hits.append({"block": bid, "why": "defined here", "source": t.get("source"), "definition_text": t.get("definition_text", ""), "score": 0})
    rx = _term_regex(key, [])
    for b in pointer_blocks or []:
        if b.id not in seen:
            seen.add(b.id)
            hits.append({"block": b.id, "why": "cited location", "source": "pointer", "definition_text": b.text, "score": 1})
    for b in doc.blocks:
        if b.id in seen or b.kind not in ("theorem", "para"):
            continue
        if not rx.search(b.text):
            continue
        is_def = b.kind == "theorem" and b.thm_kind in ("definition", "construction")
        defining = bool(re.search(r"\b(is called|we call|we say|is said to be|is defined|define[sd]?|means|denote)\b", b.text, re.I))
        if is_def or defining:
            hits.append({"block": b.id, "why": "definition environment" if is_def else "defining sentence", "source": "paper_definition" if is_def else "paper_inline",
                         "definition_text": b.text, "score": 2 if is_def else 3})
        if len(hits) >= 6:
            break
    hits.sort(key=lambda h: h["score"])
    return hits[:4]


def lookup_symbol(doc: Document, glossary, keys: list[str]) -> list[dict]:
    hits = []
    seen = set()
    for k in keys:
        for s in glossary.symbols_for(k):
            if s.get("source") == "paper_macro":
                continue
            sig = (s.get("defined_at"), s.get("meaning", "")[:40])
            if sig in seen:
                continue
            seen.add(sig)
            hits.append({"block": s.get("defined_at"), "why": "symbol entry", "source": s.get("source"), "meaning": s.get("meaning", ""),
                         "quote": s.get("quote", ""), "confidence": s.get("confidence"), "score": 0 if k == keys[0] else 1})
    hits.sort(key=lambda h: h["score"])
    return hits[:4]


# ---------------------------------------------------------------------------
# Showing another paper's content inside this paper's cards
# ---------------------------------------------------------------------------

def retag_html(html: str, pid: str) -> str:
    """Give unit ids and hover spans from paper ``pid`` a paper-specific prefix."""
    html = re.sub(r"pa-u-(?!x)([A-Za-z0-9_\-]+)", lambda m: f"pa-u-x{pid}_{m.group(1)}", html)
    html = html.replace('<span class="pa-term', f'<span data-paper="{pid}" class="pa-term')
    html = html.replace('<a class="pa-ref"', f'<a data-paper="{pid}" class="pa-ref"')
    html = html.replace('<span class="pa-cite"', f'<span data-paper="{pid}" class="pa-cite"')
    return html


def retag_units(units: dict, pid: str) -> dict:
    out = {}
    for uid, v in units.items():
        u = str(uid) if str(uid).startswith("x") else f"x{pid}_{uid}"
        vals = list(v) + [""] * (5 - len(v))
        vals[4] = pid
        out[u] = vals
    return out


def block_units(doc: Document, bid: str, pid: str) -> dict:
    out = {}
    for mid, item in doc.math.items():
        if item.block != bid:
            continue
        for u in item.units:
            out[f"x{pid}_{u['id']}"] = [u["key"], mid, u["base"], u["tex"], pid]
    return out


HTML_FIELDS = ("html", "definition_html", "meaning_html", "quote_html", "tex_html", "formula_html", "evidence_html")


def retag_card(card: dict, pid: str) -> dict:
    """Rewrite a card produced by paper ``pid``'s resolver so it can be shown while reading another paper."""
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in HTML_FIELDS and isinstance(v, str):
                    obj[k] = retag_html(v, pid)
                elif k == "units" and isinstance(v, dict):
                    obj[k] = retag_units(v, pid)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)
    walk(card)
    card["paper"] = pid
    return card
