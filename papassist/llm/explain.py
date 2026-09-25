"""On-demand questions to the LLM, always answered from the paper's text."""
from __future__ import annotations

import hashlib

from ..library.store import Paper
from ..render.page import render_snippet
from .enrich import INSTRUCTIONS as GLOSSARY_INSTRUCTIONS
from .paper_text import context_window, paper_text

EXPLAIN_INSTRUCTIONS = """You are helping a mathematician read one specific paper, whose full text follows (blocks labelled [b12 | ...]).
Answer questions about what a symbol or term means IN THIS PAPER, using only the paper's text.
If the text establishes the meaning (explicitly, or unambiguously from how it is used), set found=true, give the meaning in one or two sentences (math in $...$), list the block ids that support it, and quote the key phrase verbatim in evidence_quote.
If the paper does not define or determine it, set found=false, leave meaning empty, and in note say briefly what the paper does do (e.g. "used without definition; introduced with the citation [Chazal et al. 2016]").
Never supply a definition from outside the paper."""

EXPLAIN_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"}, "meaning": {"type": "string"},
        "evidence_blocks": {"type": "array", "items": {"type": "string"}}, "evidence_quote": {"type": "string"}, "note": {"type": "string"},
    },
    "required": ["found", "meaning", "evidence_blocks", "evidence_quote", "note"],
    "additionalProperties": False,
}

PARAPHRASE_INSTRUCTIONS = """You restate mathematical definitions in plain English for a reader who knows mathematics but is new to this paper's area.
Rules: rephrase only what the given text says; do not add facts, examples, or context that are not in it; keep every mathematical symbol as LaTeX in $...$; two to four sentences; if the text is not a definition, restate what it does say."""


def _system(paper: Paper, client, instructions: str) -> list[dict]:
    return client.system_blocks(instructions, paper_text(paper.doc))


def explain_in_context(paper: Paper, resolver, kind: str, key: str, tex: str, block: str | None) -> dict:
    doc = paper.doc
    ctx = context_window(doc, block) if block else ""
    what = f"the symbol ${tex or key}$" if kind == "symbol" else f"the term “{key}”"
    where = f" as used in block {block}" if block else ""
    user = (f"What does {what} mean in this paper{where}?\n\n"
            + (f"Local context:\n{ctx}\n\n" if ctx else "")
            + "Answer as JSON.")
    cache_key = "explain-" + hashlib.sha1((client_model(resolver) + kind + key + (block or "")).encode("utf-8")).hexdigest()[:16]
    cached = paper.cache_get(cache_key)
    if cached is None:
        data = resolver.llm.complete_json(_system(paper, resolver.llm, EXPLAIN_INSTRUCTIONS), user, EXPLAIN_SCHEMA, max_tokens=2000)
        cached = {"data": data, "usage": dict(getattr(resolver.llm, "last_usage", {}) or {})}
        paper.cache_put(cache_key, cached)
    data = cached["data"]
    found = bool(data.get("found")) and bool((data.get("meaning") or "").strip())
    meaning_html, units = render_snippet(data.get("meaning", ""), "l" + cache_key[-6:] + "-", resolver.matcher) if found else ("", {})
    evidence_html, u2 = render_snippet(data.get("evidence_quote", ""), "e" + cache_key[-6:] + "-", None) if data.get("evidence_quote") else ("", {})
    units.update(u2)
    blocks = [b for b in data.get("evidence_blocks", []) if doc.block_by_id(b) is not None]
    return {"found": found, "meaning_html": meaning_html, "meaning_text": data.get("meaning", ""), "evidence_html": evidence_html,
            "evidence_blocks": blocks, "note": data.get("note", ""), "source": "llm_context", "units": units, "usage": cached.get("usage", {})}


def paraphrase_plain(paper: Paper, resolver, text: str, context: str) -> dict:
    cache_key = "para-" + hashlib.sha1((client_model(resolver) + text).encode("utf-8")).hexdigest()[:16]
    cached = paper.cache_get(cache_key)
    if cached is None:
        user = f"Restate this in plain English{(' (it is about ' + context + ')') if context else ''}:\n\n{text}"
        out = resolver.llm.complete_text(resolver.llm.system_blocks(PARAPHRASE_INSTRUCTIONS), user, max_tokens=1200)
        cached = {"text": out, "usage": dict(getattr(resolver.llm, "last_usage", {}) or {})}
        paper.cache_put(cache_key, cached)
    html, units = render_snippet(cached["text"], "p" + cache_key[-6:] + "-", resolver.matcher)
    return {"html": html, "text": cached["text"], "units": units, "source": "llm_paraphrase", "usage": cached.get("usage", {})}


def client_model(resolver) -> str:
    return getattr(resolver.llm, "model", "none")
