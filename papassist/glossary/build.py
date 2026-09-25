"""Merge the extractors into one glossary and index every occurrence."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ingest.document import Document
from .patterns import SymbolEntry, extract_macro_symbols, extract_symbols
from .terms import TermEntry, extract_terms, term_symbol_links
from .patterns import symbol_shape

_DICT_PATH = Path(__file__).with_name("dictionary.json")
_DICT_CACHE: Optional[dict] = None


def load_dictionary() -> dict:
    global _DICT_CACHE
    if _DICT_CACHE is None:
        _DICT_CACHE = json.loads(_DICT_PATH.read_text(encoding="utf-8"))
    return _DICT_CACHE


@dataclass
class Glossary:
    symbols: list[dict] = field(default_factory=list)
    terms: list[dict] = field(default_factory=list)
    index: dict[str, list[list]] = field(default_factory=dict)      # key -> [[block, mid, uid], ...]
    base_index: dict[str, list[list]] = field(default_factory=dict) # base key -> same
    llm: dict = field(default_factory=dict)                          # status of the LLM enrichment
    built_at: float = 0.0

    def to_dict(self) -> dict:
        return {"symbols": self.symbols, "terms": self.terms, "index": self.index, "base_index": self.base_index,
                "llm": self.llm, "built_at": self.built_at}

    @classmethod
    def from_dict(cls, d: dict) -> "Glossary":
        return cls(symbols=d.get("symbols", []), terms=d.get("terms", []), index=d.get("index", {}),
                   base_index=d.get("base_index", {}), llm=d.get("llm", {}), built_at=d.get("built_at", 0.0))

    # -- lookups -----------------------------------------------------------
    def symbols_for(self, key: str) -> list[dict]:
        return [s for s in self.symbols if s["key"] == key or key in s.get("keys", [])]

    def term(self, key: str) -> list[dict]:
        return [t for t in self.terms if t["term"] == key or key in t.get("aliases", [])]


def build_index(doc: Document) -> tuple[dict, dict]:
    index: dict[str, list[list]] = {}
    base_index: dict[str, list[list]] = {}
    for mid, item in doc.math.items():
        for u in item.units:
            index.setdefault(u["key"], []).append([item.block, mid, u["id"]])
            if u["base"] != u["key"]:
                base_index.setdefault(u["base"], []).append([item.block, mid, u["id"]])
    return index, base_index


def dedupe_symbols(entries: list[SymbolEntry]) -> list[dict]:
    """Merge entries with the same key, block and pattern family; keep the best."""
    rank = {"high": 0, "medium": 1, "low": 2}
    out: dict[tuple, SymbolEntry] = {}
    for e in entries:
        k = (e.key, e.defined_at, e.meaning.lower()[:60])
        if k in out and rank[out[k].confidence] <= rank[e.confidence]:
            continue
        out[k] = e
    result = [e.to_dict() for e in out.values()]
    return result


def symbols_from_term_links(doc: Document) -> list[SymbolEntry]:
    out: list[SymbolEntry] = []
    for bid, mid, phrase, text in term_symbol_links(doc):
        item = doc.math.get(mid)
        if item is None:
            continue
        shape = symbol_shape(item.tex)
        tex = item.tex
        note = ""
        if shape is None:
            from .patterns import top_level_split, cut_rhs
            split = top_level_split(item.tex)
            if split is None:
                continue
            lhs, rel, rhs = split
            shape = symbol_shape(lhs)
            if shape is None:
                continue
            tex = lhs.strip()
            note = f"; given by ${lhs.strip()} = {cut_rhs(rhs)}$" if rel == "=" else ""
        out.append(SymbolEntry(key=shape.key, tex=tex, meaning=f"the {phrase}{note}", source="paper_pattern", confidence="high",
                               defined_at=bid, scope={"kind": "paper"}, quote=text[:300], keys=shape.extra_keys, pattern="term_symbol", math_id=mid))
    return out


def build_glossary(doc: Document) -> Glossary:
    symbols = extract_symbols(doc) + extract_macro_symbols(doc) + symbols_from_term_links(doc)
    terms = extract_terms(doc)
    index, base_index = build_index(doc)
    g = Glossary(symbols=dedupe_symbols(symbols), terms=[t.to_dict() for t in terms], index=index, base_index=base_index,
                 llm={"status": "not_run"}, built_at=time.time())
    return g
