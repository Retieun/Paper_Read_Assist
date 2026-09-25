"""The resolution chain: from a hovered unit or term to a card.

Order of trust:

1. an in-paper entry whose scope contains the hovered block (most specific first);
2. an in-paper entry with paper-wide scope;
3. the paper's macro table;
4. the standard-notation dictionary;
5. (optional, cached) the LLM reading the paper's own text, only if it finds the
   meaning there;
6. otherwise: "not defined in this paper", with where the symbol first appears.

Nothing here invents a definition.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from ..glossary.build import Glossary, load_dictionary
from ..glossary.textutil import canonical_term
from ..ingest.document import Document
from ..render.mathunits import normalize_tex
from ..render.page import build_matcher, render_snippet, wrap_terms_html
from ..render.terms import TermMatcher

SOURCE_LABELS = {
    "paper_pattern": ("This paper", "paper"),
    "paper_definition": ("This paper", "paper"),
    "paper_inline": ("This paper", "paper"),
    "paper_macro": ("This paper (macro)", "paper"),
    "dictionary": ("Standard notation", "dictionary"),
    "llm": ("Inferred from this paper's text (LLM)", "llm"),
    "llm_context": ("Inferred from this paper's text (LLM)", "llm"),
}
SOURCE_RANK = {"paper_definition": 0, "paper_pattern": 1, "paper_inline": 1, "llm": 2, "paper_macro": 3, "dictionary": 4}
PATTERN_RANK = {"term_symbol": 0, "let_be": 0, "write_for": 0, "denote_by": 0, "defined_as": 0, "where_is": 1, "map_signature": 1,
                "display_equation": 1, "let_elided": 2, "x_denotes": 2, "element_of": 2, "appositive": 3, "macro": 4, "llm": 1}
CONF_RANK = {"high": 0, "medium": 1, "low": 2}


class Resolver:
    def __init__(self, doc: Document, glossary: Glossary, llm=None):
        self.doc = doc
        self.glossary = glossary
        self.dictionary = load_dictionary()
        self.llm = llm
        self.matcher: TermMatcher = build_matcher(glossary)
        self._block_pos = {b.id: i for i, b in enumerate(doc.blocks)}
        self._unit_lookup: dict[str, tuple[str, dict]] = {}
        for mid, item in doc.math.items():
            for u in item.units:
                self._unit_lookup[str(u["id"])] = (mid, u)

    def refresh(self, glossary: Glossary) -> None:
        self.glossary = glossary
        self.matcher = build_matcher(glossary)

    # -- helpers --------------------------------------------------------------
    def _card_prefix(self, seed: str) -> str:
        return "c" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6] + "-"

    def _location(self, bid: Optional[str]) -> Optional[dict]:
        if not bid:
            return None
        b = self.doc.block_by_id(bid)
        if b is None:
            return None
        heading = b.heading or (f"§{b.section}" if b.section else "")
        if not heading:
            heading = "paragraph" + (f" in §{b.section}" if b.section else "")
        return {"block": bid, "heading": heading, "kind": b.kind, "thm_kind": b.thm_kind, "number": b.number, "section": b.section}

    def _in_scope(self, entry: dict, bid: Optional[str]) -> bool:
        scope = entry.get("scope") or {"kind": "paper"}
        if scope.get("kind") != "blocks" or bid is None:
            return True
        return bid in scope.get("blocks", [])

    def _rank(self, entry: dict, bid: Optional[str]):
        scope = entry.get("scope") or {"kind": "paper"}
        specific = 0 if scope.get("kind") == "blocks" else 1
        src = SOURCE_RANK.get(entry.get("source", ""), 5)
        pat = PATTERN_RANK.get(entry.get("pattern", ""), 3)
        conf = CONF_RANK.get(entry.get("confidence", "low"), 2)
        dist = 0
        if bid is not None and entry.get("defined_at") in self._block_pos:
            d = self._block_pos[bid] - self._block_pos[entry["defined_at"]]
            dist = d if d >= 0 else 1000 + abs(d)
        return (specific, src, pat, conf, dist)

    def _snippet(self, text: str, seed: str) -> tuple[str, dict]:
        return render_snippet(text, self._card_prefix(seed), self.matcher)

    def _entry_view(self, e: dict, seed: str) -> tuple[dict, dict]:
        label, cls = SOURCE_LABELS.get(e.get("source", ""), ("Unknown source", "unknown"))
        meaning_html, units = self._snippet(e.get("meaning", ""), seed + e.get("meaning", ""))
        quote_html, units2 = self._snippet(e.get("quote", ""), seed + "q" + e.get("quote", "")) if e.get("quote") else ("", {})
        units.update(units2)
        view = {
            "meaning_html": meaning_html, "meaning_text": e.get("meaning", ""), "source": e.get("source"),
            "source_label": label, "source_class": cls, "confidence": e.get("confidence"), "pattern": e.get("pattern"),
            "location": self._location(e.get("defined_at")), "quote_html": quote_html, "scope": e.get("scope"),
            "category": e.get("category", ""),
        }
        return view, units

    # -- occurrences -----------------------------------------------------------
    def occurrences(self, keys: list[str]) -> dict:
        blocks: list[str] = []
        uids: list[str] = []
        seen_blocks = set()
        count = 0
        for k in keys:
            for bid, mid, uid in self.glossary.index.get(k, []):
                count += 1
                uids.append(str(uid))
                if bid not in seen_blocks:
                    seen_blocks.add(bid)
                    blocks.append(bid)
        blocks.sort(key=lambda b: self._block_pos.get(b, 10**9))
        first = self._location(blocks[0]) if blocks else None
        return {"count": count, "blocks": blocks[:60], "first": first}

    # -- symbols ---------------------------------------------------------------
    def resolve_unit(self, uid: str, bid: Optional[str]) -> dict:
        if uid in self._unit_lookup:
            mid, u = self._unit_lookup[uid]
            return self.resolve_symbol(u["key"], list(u.get("keys", [u["key"]])), u["tex"], bid, mid=mid, base=u.get("base"))
        return {"kind": "symbol", "status": "not_found", "tex": "", "key": "", "entries": [], "message": "Unknown unit."}

    def resolve_by_key(self, key: str, tex: str, base: Optional[str], bid: Optional[str]) -> dict:
        """Resolve a unit that is not in the paper's unit table (e.g. one rendered inside a card)."""
        keys = [key]
        try:
            from ..render.mathunits import analyze_math

            a = analyze_math(tex or key)
            tops = [u for u in a.units if u.parent is None]
            if tops:
                keys = list(dict.fromkeys([key, *tops[0].keys]))
                base = base or tops[0].base
        except Exception:
            pass
        if base and base not in keys:
            keys.append(base)
        return self.resolve_symbol(key, keys[1:], tex or key, bid, base=base)

    def resolve_symbol(self, key: str, keys: list[str], tex: str, bid: Optional[str], mid: Optional[str] = None, base: Optional[str] = None) -> dict:
        keys = list(dict.fromkeys([key, *keys]))
        card_units: dict = {}
        found: list[tuple[tuple, dict, str]] = []
        for i, k in enumerate(keys):
            for e in self.glossary.symbols_for(k):
                if e.get("source") == "paper_macro":
                    continue
                if not self._in_scope(e, bid):
                    continue
                found.append(((i,) + self._rank(e, bid), e, k))
        found.sort(key=lambda x: x[0])
        entries = []
        seen_meanings: list[str] = []
        for _, e, matched_key in found:
            mk = re.sub(r"[^a-z ]", "", e.get("meaning", "").lower()).strip()
            head = " ".join(mk.split()[:3])
            if any(head and (h.startswith(head) or head.startswith(h)) for h in seen_meanings):
                continue
            seen_meanings.append(head or mk)
            view, units = self._entry_view(e, key + e.get("defined_at", "") + matched_key)
            view["matched_key"] = matched_key
            view["exact"] = matched_key == key
            card_units.update(units)
            entries.append(view)
            if len(entries) >= 3:
                break
        # macro
        macro = None
        for name, m in self.doc.macros.items():
            if m.get("nargs"):
                continue
            try:
                if normalize_tex(m.get("body", "")) in keys:
                    macro = {"name": name, "body": m.get("body", "")}
                    break
            except Exception:
                continue
        # dictionary
        dictionary = None
        dsyms = self.dictionary.get("symbols", {})
        for k in keys:
            d = dsyms.get(k)
            matched = k
            if d is None:
                best = None
                for pat_key, cand in dsyms.items():
                    if "*" in pat_key and self._wild_match(pat_key, k):
                        literal = len(pat_key.replace("*", ""))
                        if best is None or literal > best[0]:
                            best = (literal, pat_key, cand)
                if best is not None:
                    _, matched, d = best
            if d is not None:
                mh, du = self._snippet(d.get("meaning", ""), "dict" + matched)
                card_units.update(du)
                dictionary = {"key": matched, "name": d.get("name", ""), "meaning_html": mh, "meaning_text": d.get("meaning", ""), "exact": k == key}
                break
        occ = self.occurrences(keys[:1]) if keys else {"count": 0, "blocks": [], "first": None}
        occ_base = self.occurrences([base]) if base and base != key else None
        # related: other keys with the same base
        related = []
        if base:
            for k2 in self.glossary.base_index:
                pass
            for k2, hits in self.glossary.index.items():
                if k2 != key and (k2.startswith(base + "_") or k2.startswith(base + "^") or k2 == base) and len(related) < 8:
                    related.append({"key": k2, "tex": self._tex_for_key(k2, hits), "count": len(hits)})
        status = "found" if entries else ("dictionary" if dictionary else "not_found")
        tex_html, tu = self._snippet(f"${tex}$", "title" + key)
        card_units.update(tu)
        return {
            "kind": "symbol", "key": key, "keys": keys, "tex": tex, "tex_html": tex_html, "base": base, "mid": mid,
            "status": status, "entries": entries, "macro": macro, "dictionary": dictionary,
            "occurrences": occ, "occurrences_base": occ_base, "related": related, "units": card_units,
            "llm_available": bool(self.llm),
        }

    @staticmethod
    def _wild_match(pattern: str, key: str) -> bool:
        rx = "^" + ".*?".join(re.escape(part) for part in pattern.split("*")) + "$"
        bare = key.replace("'", "").replace("\\prime", "")
        return re.match(rx, key) is not None or re.match(rx, bare) is not None

    def _tex_for_key(self, key: str, hits: list) -> str:
        for bid, mid, uid in hits[:1]:
            item = self.doc.math.get(mid)
            if item:
                for u in item.units:
                    if u["id"] == uid:
                        return u["tex"]
        return key

    def resolve_operator(self, char: str) -> dict:
        ops = self.dictionary.get("operators", {})
        d = ops.get(char)
        if d is None:
            return {"kind": "operator", "char": char, "status": "not_found", "entries": [], "tex": "", "units": {}}
        mh, units = self._snippet(d.get("meaning", ""), "op" + char)
        return {"kind": "operator", "char": char, "tex": d.get("tex", ""), "name": d.get("name", ""), "status": "dictionary",
                "meaning_html": mh, "meaning_text": d.get("meaning", ""), "units": units}

    def _unit_rows(self, item, units: list[dict], bid: Optional[str], mid: str) -> list[dict]:
        rows = []
        seen = set()
        for u in units:
            if u["key"] in seen:
                continue
            seen.add(u["key"])
            card = self.resolve_symbol(u["key"], list(u.get("keys", [])), u["tex"], bid, mid=mid, base=u.get("base"))
            best = card["entries"][0]["meaning_text"] if card["entries"] else (card["dictionary"]["meaning_text"] if card.get("dictionary") else "")
            mh, _ = self._snippet(best[:200], "row" + u["key"] + best[:40]) if best else ("", {})
            rows.append({"uid": str(u["id"]), "tex": u["tex"], "key": u["key"], "status": card["status"], "meaning": best[:200], "meaning_html": mh})
        return rows

    @staticmethod
    def _math_html(item) -> str:
        if item.bare or item.display:
            return f'<span class="pa-math pa-display">{_esc(item.tagged)}</span>'
        return f'<span class="pa-math">\\({_esc(item.tagged)}\\)</span>'

    def resolve_formula(self, mid: str, bid: Optional[str]) -> dict:
        item = self.doc.math.get(mid)
        if item is None:
            return {"kind": "formula", "status": "not_found", "units": {}, "items": []}
        rows = self._unit_rows(item, [u for u in item.units if u.get("parent") is None], bid, mid)
        return {"kind": "formula", "mid": mid, "status": "found" if rows else "not_found", "items": rows,
                "formula_html": self._math_html(item), "units": {}}

    def resolve_range(self, mid: str, uid1: str, uid2: str, bid: Optional[str]) -> dict:
        """Shift-click selection: the sub-expression spanned by two units of one formula."""
        item = self.doc.math.get(mid)
        if item is None:
            return {"kind": "range", "status": "not_found", "units": {}, "items": [], "entries": []}
        by_id = {str(u["id"]): u for u in item.units}
        a, b = by_id.get(uid1), by_id.get(uid2)
        if a is None or b is None:
            return {"kind": "range", "status": "not_found", "units": {}, "items": [], "entries": []}
        start, end = min(a["start"], b["start"]), max(a["end"], b["end"])
        src = item.src or item.tex
        sub = src[start:end]
        # extend to balanced braces / parentheses
        while sub.count("{") > sub.count("}") and end < len(src):
            end += 1
            sub = src[start:end]
        while sub.count("(") > sub.count(")") and end < len(src):
            end += 1
            sub = src[start:end]
        while sub.count("}") > sub.count("{") and start > 0:
            start -= 1
            sub = src[start:end]
        sub = re.sub(r"\\tag\{[^}]*\}", "", sub).strip()
        key = normalize_tex(sub)
        card = self.resolve_by_key(key, sub, None, bid)
        inside = [u for u in item.units if u["start"] >= start and u["end"] <= end and (u.get("parent") is None or str(u["parent"]) not in by_id or by_id[str(u["parent"])]["start"] < start or by_id[str(u["parent"])]["end"] > end)]
        card.update({"kind": "range", "mid": mid, "items": self._unit_rows(item, inside, bid, mid)})
        return card

    # -- terms -----------------------------------------------------------------
    def resolve_term(self, term: str, bid: Optional[str]) -> dict:
        key = canonical_term(term) or term.lower().strip()
        hits = self.glossary.term(key)
        if not hits:
            # try the raw lower-case form and singular/plural variants
            hits = self.glossary.term(term.lower().strip())
        card_units: dict = {}
        entries = []
        hits.sort(key=lambda t: (0 if t.get("source") == "paper_definition" else 1, CONF_RANK.get(t.get("confidence", "low"), 2)))
        for t in hits[:3]:
            label, cls = SOURCE_LABELS.get(t.get("source", ""), ("Unknown source", "unknown"))
            block = self.doc.block_by_id(t.get("definition_block") or "")
            if t.get("source") == "paper_definition" and block is not None:
                def_html = wrap_terms_html(block.html, self.matcher, key)
            else:
                def_html, du = self._snippet(t.get("definition_text", ""), "term" + key + (t.get("definition_block") or ""))
                card_units.update(du)
            cite_hints = []
            for ck in t.get("cite_hints", []):
                e = self.doc.bibliography.get(ck)
                if e:
                    cite_hints.append({"key": ck, "short": e.get("short"), "title": e.get("title"), "arxiv": e.get("arxiv"), "doi": e.get("doi"), "year": e.get("year")})
            entries.append({
                "definition_html": def_html, "definition_text": t.get("definition_text", ""), "source": t.get("source"),
                "source_label": label, "source_class": cls, "confidence": t.get("confidence"), "display": t.get("display"),
                "location": self._location(t.get("definition_block")), "cite_hints": cite_hints, "aliases": t.get("aliases", []),
                "status": t.get("status", "defined"),
            })
        mentions = self._term_mentions(key, hits)
        depends = self._depends_on(entries, key)
        status = "found" if any(e["status"] == "defined" for e in entries) else ("undefined" if entries else "not_found")
        return {"kind": "term", "term": key, "display": (hits[0].get("display") if hits else term), "status": status,
                "entries": entries, "mentions": mentions, "depends_on": depends, "units": card_units,
                "cite_hints": entries[0]["cite_hints"] if entries else [], "llm_available": bool(self.llm)}

    def _term_mentions(self, key: str, hits: list[dict]) -> dict:
        forms = {key, *[a for t in hits for a in t.get("aliases", [])]}
        pat = re.compile(r"(?<![\w\-])(" + "|".join(re.escape(f).replace(r"\ ", r"[\s\-\u2013\u2014]+") for f in sorted(forms, key=len, reverse=True) if f) + r")s?(?![\w\-])", re.I)
        blocks = []
        count = 0
        for b in self.doc.blocks:
            n = len(pat.findall(b.text))
            if n:
                count += n
                blocks.append(b.id)
        return {"count": count, "blocks": blocks[:60], "first": self._location(blocks[0]) if blocks else None}

    def _depends_on(self, entries: list[dict], key: str) -> list[dict]:
        if not entries or self.matcher.re is None:
            return []
        text = entries[0].get("definition_text", "")
        found: dict[str, str] = {}
        for m in self.matcher.re.finditer(re.sub(r"\$[^$]*\$", " ", text)):
            hit = self.matcher.lookup(m.group(1))
            if hit and hit[0] != key:
                found.setdefault(hit[0], m.group(1))
        return [{"term": k, "display": v} for k, v in list(found.items())[:12]]

    # -- blocks / citations ------------------------------------------------------
    def block_card(self, bid: str) -> dict:
        b = self.doc.block_by_id(bid)
        if b is None:
            return {"kind": "block", "status": "not_found", "units": {}}
        return {"kind": "block", "status": "found", "block": bid, "heading": b.heading, "html": wrap_terms_html(b.html, self.matcher),
                "location": self._location(bid), "units": {}}

    def label_card(self, label: str) -> dict:
        info = self.doc.labels.get(label)
        if not info:
            return {"kind": "block", "status": "not_found", "units": {}}
        if info.get("kind") == "equation" and info.get("math"):
            item = self.doc.math.get(info["math"])
            bid = info.get("block") or (item.block if item else None)
            card = self.block_card(bid) if bid else {"kind": "block", "status": "not_found", "units": {}}
            card["heading"] = f"Equation ({info.get('number') or '?'})"
            return card
        if info.get("block"):
            return self.block_card(info["block"])
        return {"kind": "block", "status": "not_found", "units": {}}

    def cite_card(self, keys: list[str]) -> dict:
        entries = []
        for k in keys:
            e = self.doc.bibliography.get(k)
            if e:
                entries.append({"key": k, "short": e.get("short"), "title": e.get("title"), "authors": e.get("authors"), "year": e.get("year"),
                                "venue": e.get("venue"), "arxiv": e.get("arxiv"), "doi": e.get("doi"), "url": e.get("url")})
            else:
                entries.append({"key": k, "short": k, "title": "(not found in the bibliography)"})
        return {"kind": "cite", "status": "found" if entries else "not_found", "entries": entries, "units": {}}


def _esc(s: str) -> str:
    import html as h
    return h.escape(s, quote=False)
