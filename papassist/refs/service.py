"""Glue between the library, a paper's bibliography and the lookup functions."""
from __future__ import annotations

from typing import Optional

from ..ingest.bib import BibEntry
from ..library.store import Library, Paper
from .arxiv import resolve_arxiv_id
from .lookup import block_units, find_block_by_pointer, lookup_symbol, lookup_term, parse_pointer, retag_card, retag_html


def references_status(library: Library, paper: Paper) -> list[dict]:
    """Every cited work with its label, arXiv id (if known) and library status."""
    doc = paper.doc
    meta = paper.meta
    ref_ids = meta.get("ref_ids", {})
    labels = doc.cite_labels or {}
    keys = list(labels.keys()) or sorted(doc.bibliography.keys())
    keys.sort(key=lambda k: (len(labels.get(k, "")), labels.get(k, "")) if labels.get(k, "").isdigit() else (99, labels.get(k, k)))
    out = []
    by_arxiv = {m.get("arxiv"): m["id"] for m in library.list() if m.get("arxiv")}
    for k in keys:
        e = doc.bibliography.get(k)
        entry = BibEntry(**{kk: vv for kk, vv in e.items() if kk != "short"}) if e else None
        aid, how = (None, "none")
        if k in ref_ids:
            aid, how = ref_ids[k].get("arxiv"), ref_ids[k].get("method", "cached")
        elif entry is not None:
            aid, how = resolve_arxiv_id(entry, allow_search=False)
        out.append({
            "key": k, "label": labels.get(k, k), "short": (e or {}).get("short", k), "title": (e or {}).get("title", ""),
            "year": (e or {}).get("year"), "authors": (e or {}).get("authors", []), "arxiv": aid, "arxiv_method": how,
            "doi": (e or {}).get("doi"), "in_library": by_arxiv.get(aid) if aid else None,
        })
    return out


def cite_pointers(paper: Paper, key: str) -> list[dict]:
    ptrs = []
    for b in paper.doc.blocks:
        for c in b.cites:
            if c.get("key") == key and c.get("suffix"):
                ptrs.extend(parse_pointer(c["suffix"]))
    return ptrs


def lookup_in_library(library: Library, paper: Paper, get_resolver, term: Optional[str] = None, keys: Optional[list[str]] = None,
                      ref_key: Optional[str] = None, only_keys: Optional[list[str]] = None) -> list[dict]:
    """Search cited papers that are in the library. Returns cards from those papers, re-tagged for this reader."""
    refs = references_status(library, paper)
    results = []
    for r in refs:
        if not r["in_library"]:
            continue
        if ref_key and r["key"] != ref_key:
            continue
        if only_keys is not None and r["key"] not in only_keys:
            continue
        other = library.get(r["in_library"])
        if other is None or other.id == paper.id:
            continue
        pblocks = [blk for ptr in cite_pointers(paper, r["key"]) if (blk := find_block_by_pointer(other.doc, ptr))]
        res = get_resolver(other.id)
        if term:
            hits = lookup_term(other.doc, other.glossary, term, pblocks)
        else:
            hits = lookup_symbol(other.doc, other.glossary, keys or [])
        for h in hits:
            bid = h.get("block")
            entry = {
                "paper": other.id, "paper_title": other.meta.get("title", other.id), "label": r["label"], "short": r["short"],
                "key": r["key"], "why": h.get("why"), "source": h.get("source"), "block": bid, "units": {},
            }
            if term:
                if not bid:
                    continue
                card = retag_card(res.block_card(bid), other.id)
                entry.update({"heading": card.get("heading") or "", "html": card.get("html", ""), "units": card.get("units", {}),
                              "location": card.get("location")})
                entry["units"].update(block_units(other.doc, bid, other.id))
            else:
                mh, units = res._snippet(h.get("meaning", ""), "x" + r["key"] + h.get("meaning", ""))
                qh, units2 = res._snippet(h.get("quote", ""), "xq" + r["key"] + h.get("quote", "")) if h.get("quote") else ("", {})
                units.update(units2)
                from .lookup import retag_units
                entry.update({"meaning_html": retag_html(mh, other.id), "meaning_text": h.get("meaning", ""), "quote_html": retag_html(qh, other.id),
                              "units": retag_units(units, other.id), "location": res._location(bid) if bid else None,
                              "confidence": h.get("confidence")})
            results.append(entry)
    return results


def attach_reference_info(card: dict, library: Library, paper: Paper, get_resolver) -> dict:
    """Add cited-paper hits and reference buttons to a term/symbol card."""
    if card.get("kind") not in ("term", "symbol", "range"):
        return card
    refs = references_status(library, paper)
    by_key = {r["key"]: r for r in refs}
    hint_keys: list[str] = []
    if card["kind"] == "term":
        hint_keys = [c["key"] for c in card.get("cite_hints", []) if c.get("key")]
        for e in card.get("entries", []):
            for c in e.get("cite_hints", []):
                if c.get("key") and c["key"] not in hint_keys:
                    hint_keys.append(c["key"])
    card["references"] = [by_key[k] for k in hint_keys if k in by_key]
    card["library_refs"] = sum(1 for r in refs if r["in_library"])
    try:
        if card["kind"] == "term":
            only = hint_keys if card.get("status") == "found" else None
            if only or card.get("status") != "found":
                card["cited"] = lookup_in_library(library, paper, get_resolver, term=card.get("term"), only_keys=only)
        elif card.get("status") == "not_found":
            card["cited"] = lookup_in_library(library, paper, get_resolver, keys=card.get("keys") or [card.get("key")])
    except Exception:
        card["cited"] = []
    return card
