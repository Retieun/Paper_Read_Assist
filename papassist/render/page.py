"""Assemble what the browser needs: block HTML with term spans, unit maps, snippets."""
from __future__ import annotations

import html as htmllib
import re
from typing import Optional

from ..glossary.build import Glossary
from ..glossary.textutil import term_variants
from ..ingest.ast_to_doc import label_id
from ..ingest.document import Document
from .mathunits import analyze_math
from .terms import TermMatcher, wrap_terms_html


def build_matcher(glossary: Glossary) -> TermMatcher:
    variants: dict[str, tuple[str, str]] = {}
    for t in glossary.terms:
        status = t.get("status", "defined")
        for form in [t["term"], *t.get("aliases", [])]:
            if not form or len(form) < 3:
                continue
            for v in term_variants(form):
                if len(v) < 3:
                    continue
                # a defined term wins over an undefined mention of the same words
                cur = variants.get(v)
                if cur is None or (cur[1] == "undefined" and status == "defined"):
                    variants[v] = (t["term"], status)
    return TermMatcher(variants)


def block_payload(doc: Document, glossary: Glossary, matcher: Optional[TermMatcher] = None) -> list[dict]:
    matcher = matcher or build_matcher(glossary)
    def_keys: dict[str, str] = {}
    for t in glossary.terms:
        if t.get("definition_block"):
            def_keys.setdefault(t["definition_block"], t["term"])
    out = []
    for b in doc.blocks:
        html = wrap_terms_html(b.html, matcher, def_keys.get(b.id))
        out.append({
            "id": b.id, "kind": b.kind, "thm_kind": b.thm_kind, "env": b.env, "number": b.number, "label": b.label,
            "label_id": label_id(b.label) if b.label else None, "heading": b.heading, "section": b.section,
            "level": b.level, "html": html, "title": b.title,
        })
    return out


def units_payload(doc: Document) -> dict:
    """uid -> [key, mid, base] for every tagged unit in the paper."""
    out: dict = {}
    for mid, item in doc.math.items():
        for u in item.units:
            out[str(u["id"])] = [u["key"], mid, u["base"]]
    return out


def toc_payload(doc: Document) -> list[dict]:
    return [{"id": b.id, "number": b.number, "text": b.text, "level": b.level} for b in doc.blocks if b.kind == "heading"]


_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)


def render_snippet(text: str, id_prefix: str, matcher: Optional[TermMatcher] = None) -> tuple[str, dict]:
    """Render plain text with $math$ into HTML with hoverable formulas.

    Returns (html, units) where units maps the new unit ids to [key, '', base].
    """
    units: dict = {}
    out = []
    pos = 0
    counter = [1]

    def render_math(tex: str, display: bool) -> str:
        a = analyze_math(tex.strip(), 1)
        # give card units their own ids
        tagged = a.tagged
        mapping = {}
        for u in a.units:
            new_id = f"{id_prefix}{counter[0]}"
            counter[0] += 1
            mapping[u.id] = new_id
            units[new_id] = [u.key, "", u.base]
        tagged = re.sub(r"\\class\{pa-u-(\d+)\}", lambda m: "\\class{pa-u-" + mapping.get(int(m.group(1)), m.group(1)) + "}", tagged)
        cls = "pa-math pa-display" if display else "pa-math"
        delim = ("\\[", "\\]") if display else ("\\(", "\\)")
        return f'<span class="{cls}">{delim[0]}{htmllib.escape(tagged, quote=False)}{delim[1]}</span>'

    for m in _MATH_RE.finditer(text):
        chunk = text[pos:m.start()]
        out.append(matcher.wrap_text(chunk) if matcher else htmllib.escape(chunk, quote=False))
        if m.group(1) is not None:
            out.append(render_math(m.group(1), True))
        else:
            # long inline formulas are shown displayed so they can scroll instead of breaking the card layout
            out.append(render_math(m.group(2), len(m.group(2)) > 70))
        pos = m.end()
    chunk = text[pos:]
    out.append(matcher.wrap_text(chunk) if matcher else htmllib.escape(chunk, quote=False))
    html = "".join(out).replace("\n", "<br>")
    return html, units
