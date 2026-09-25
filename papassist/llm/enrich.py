"""The LLM glossary pass: what the paper itself says about its notation and terms."""
from __future__ import annotations

import hashlib
import time

from ..glossary.build import Glossary, build_index
from ..glossary.patterns import SymbolEntry, symbol_shape, top_level_split
from ..glossary.terms import TermEntry, clean_key
from ..glossary.textutil import canonical_term
from ..library.store import Paper
from .paper_text import paper_text

PROMPT_VERSION = "v1"

INSTRUCTIONS = """You are helping a mathematician read one specific paper. You will receive the paper's full text, with every block labelled like [b12 | Definition 3.3 (...) | in §3.1].

Your job is to build a glossary of the paper's NOTATION and TECHNICAL TERMS using only what this paper's text states or makes unambiguous. Rules:
- A meaning must be supported by the paper's own words. Quote the supporting phrase in `evidence` (verbatim, short). If the paper never says what a symbol stands for, do not invent a meaning: leave it out.
- Do not use outside knowledge to define anything. You may use outside knowledge only to recognise that a symbol is standard (e.g. tensor product), and then say so in `meaning` with the words "standard notation".
- Symbols: record named objects, maps, functors, indices with a fixed role, constants, and notation introduced with "let", "denote", "write", ":=", "where ... is". Write `tex` exactly as the paper writes it (LaTeX, no $). If a symbol has a paper-wide meaning use scope "paper"; if it is bound inside one statement or proof use scope "local" and list those block ids in `scope_blocks`. `defined_at` is the block id where the meaning is established, or "" if none.
- Terms: every technical term the paper defines (definition environments, "we say that ... is", "is called", emphasised phrases) with `defined` true, `definition_block` the block id, and `definition` a faithful summary in the paper's words (LaTeX allowed, math in $...$). Also list terms the paper uses as technical vocabulary without defining them, with `defined` false, `definition` "", and in `cite_keys` the bibliography keys the paper cites when introducing them (from the [Author Year] markers; give the citation text if you cannot tell the key). `depends_on` lists other terms the definition relies on.
- Be thorough but precise: several hundred entries are fine for a long paper. Prefer many short, exact meanings over few long ones."""

SCHEMA = {
    "type": "object",
    "properties": {
        "symbols": {"type": "array", "items": {"type": "object", "properties": {
            "tex": {"type": "string"}, "meaning": {"type": "string"}, "category": {"type": "string"},
            "scope": {"type": "string", "enum": ["paper", "local"]}, "scope_blocks": {"type": "array", "items": {"type": "string"}},
            "defined_at": {"type": "string"}, "evidence": {"type": "string"}},
            "required": ["tex", "meaning", "category", "scope", "scope_blocks", "defined_at", "evidence"], "additionalProperties": False}},
        "terms": {"type": "array", "items": {"type": "object", "properties": {
            "term": {"type": "string"}, "defined": {"type": "boolean"}, "definition_block": {"type": "string"},
            "definition": {"type": "string"}, "cite_keys": {"type": "array", "items": {"type": "string"}},
            "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["term", "defined", "definition_block", "definition", "cite_keys", "depends_on"], "additionalProperties": False}},
    },
    "required": ["symbols", "terms"],
    "additionalProperties": False,
}


def _valid_block(doc, bid: str) -> str:
    return bid if bid and doc.block_by_id(bid) is not None else ""


def merge_llm_output(paper: Paper, data: dict, model: str, usage: dict) -> Glossary:
    doc = paper.doc
    g = paper.glossary
    symbols = [s for s in g.symbols if s.get("source") != "llm"]
    terms = [t for t in g.terms if t.get("source") != "llm"]
    existing_terms = {t["term"] for t in terms}
    added_s = added_t = 0
    for s in data.get("symbols", []):
        tex = (s.get("tex") or "").strip().strip("$")
        meaning = (s.get("meaning") or "").strip()
        if not tex or not meaning:
            continue
        shape = symbol_shape(tex)
        if shape is None:
            split = top_level_split(tex)
            if split:
                shape = symbol_shape(split[0])
                tex = split[0].strip()
        if shape is None:
            continue
        defined_at = _valid_block(doc, s.get("defined_at", ""))
        blocks = [b for b in s.get("scope_blocks", []) if _valid_block(doc, b)]
        scope = {"kind": "blocks", "blocks": blocks} if s.get("scope") == "local" and blocks else {"kind": "paper"}
        conf = "high" if s.get("evidence") else "medium"
        symbols.append(SymbolEntry(key=shape.key, tex=tex, meaning=meaning, source="llm", confidence=conf, defined_at=defined_at or None,
                                   scope=scope, quote=s.get("evidence", ""), category=s.get("category", ""), keys=shape.extra_keys,
                                   pattern="llm").to_dict())
        added_s += 1
    for t in data.get("terms", []):
        raw = (t.get("term") or "").strip()
        key = clean_key(canonical_term(raw))
        if not key or len(key) < 3:
            continue
        defined = bool(t.get("defined"))
        if defined and key in existing_terms:
            # keep the deterministic entry; add the LLM summary only as a second view
            pass
        dblock = _valid_block(doc, t.get("definition_block", ""))
        terms.append(TermEntry(term=key, display=raw, definition_block=dblock or None, definition_text=t.get("definition", "") or "",
                               source="llm", confidence="medium" if defined else "low", aliases=[], cite_hints=list(t.get("cite_keys", [])),
                               status="defined" if defined else "undefined", section="").to_dict())
        added_t += 1
    index, base_index = build_index(doc)
    return Glossary(symbols=symbols, terms=terms, index=index, base_index=base_index,
                    llm={"status": "done", "model": model, "usage": usage, "symbols_added": added_s, "terms_added": added_t,
                         "at": time.time(), "prompt_version": PROMPT_VERSION}, built_at=g.built_at)


def enrich_glossary(paper: Paper, client) -> Glossary:
    doc = paper.doc
    text = paper_text(doc)
    cache_key = "enrich-" + hashlib.sha1((PROMPT_VERSION + client.model + text).encode("utf-8")).hexdigest()[:16]
    cached = paper.cache_get(cache_key)
    if cached is not None:
        data, usage = cached["data"], cached.get("usage", {})
    else:
        system = client.system_blocks(INSTRUCTIONS, text)
        user = "Build the glossary for this paper now. Return JSON only."
        data = client.complete_json(system, user, SCHEMA, max_tokens=64000, stream=True)
        usage = dict(getattr(client, "last_usage", {}) or {})
        paper.cache_put(cache_key, {"data": data, "usage": usage, "model": client.model})
    return merge_llm_output(paper, data, client.model, usage)
