"""The paper as the LLM sees it: every block with its id, heading and section."""
from __future__ import annotations

from ..ingest.document import Document


def paper_text(doc: Document, max_chars: int = 900_000) -> str:
    parts = [f"PAPER: {doc.title}", f"AUTHORS: {', '.join(doc.authors)}", ""]
    if doc.macros:
        macs = "; ".join(f"\\{k} -> {v.get('body', '')}" for k, v in list(doc.macros.items())[:80])
        parts.append("MACROS DEFINED BY THE AUTHORS (already expanded in the text below): " + macs)
        parts.append("")
    for b in doc.blocks:
        head = b.heading or ("§" + b.section if b.section else "")
        parts.append(f"[{b.id}" + (f" | {head}" if head else "") + (f" | in §{b.section}" if b.section and b.kind != "heading" else "") + "]")
        parts.append(b.text)
        parts.append("")
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated ...]"
    return text


def context_window(doc: Document, bid: str, before: int = 2, after: int = 1) -> str:
    i = doc.block_index(bid)
    if i < 0:
        return ""
    lo, hi = max(0, i - before), min(len(doc.blocks), i + after + 1)
    out = []
    for b in doc.blocks[lo:hi]:
        head = b.heading or ("§" + b.section if b.section else "")
        out.append(f"[{b.id}{' | ' + head if head else ''}]\n{b.text}")
    return "\n\n".join(out)
