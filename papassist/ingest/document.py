"""The document model: what PapAssist knows about a paper after ingest."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class MathItem:
    id: str
    tex: str            # TeX as written (macro-expanded by pandoc), labels removed
    display: bool
    tagged: str         # TeX with \class{pa-u-N}{...} wrappers, ready for MathJax
    bare: bool          # True when `tagged` is a top-level AMS environment (no \[ \] needed)
    block: str          # id of the block containing this formula
    units: list[dict] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Block:
    id: str
    kind: str                       # heading | para | theorem | proof | list | figure | abstract | title | other
    html: str                       # inner HTML (math as .pa-math spans; no term spans yet)
    text: str                       # plain text; inline math as $tex$, display math as $$tex$$
    text_ph: str                    # plain text with formulas replaced by ⟦mid⟧ placeholders
    section: str = ""               # section number string in force ("2.1"); "" before the first section
    env: Optional[str] = None       # theorem environment name (definition, lemma, ...)
    thm_kind: Optional[str] = None  # normalised kind (definition, theorem, proof, ...)
    thm_title: Optional[str] = None # "Definition"
    number: Optional[str] = None
    label: Optional[str] = None
    title: Optional[str] = None     # optional bracket title, plain text
    level: Optional[int] = None     # heading level
    math_ids: list[str] = field(default_factory=list)
    cites: list[dict] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    emph: list[str] = field(default_factory=list)
    strong: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def heading(self) -> str:
        """'Definition 2.3 (Persistence module)' or '' for plain blocks."""
        if self.kind == "theorem" and self.thm_title:
            h = self.thm_title + (f" {self.number}" if self.number else "")
            if self.title:
                h += f" ({self.title})"
            return h
        if self.kind == "proof":
            return "Proof"
        if self.kind == "heading":
            return (f"§{self.number} " if self.number else "") + self.text
        return ""


@dataclass
class Document:
    paper_id: str
    title: str
    authors: list[str]
    blocks: list[Block]
    math: dict[str, MathItem]
    labels: dict[str, dict]
    macros: dict[str, dict]
    theorems: dict[str, dict]
    bibliography: dict[str, dict]
    diagrams: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_main: str = ""
    documentclass: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "blocks": [b.to_dict() for b in self.blocks],
            "math": {k: v.to_dict() for k, v in self.math.items()},
            "labels": self.labels,
            "macros": self.macros,
            "theorems": self.theorems,
            "bibliography": self.bibliography,
            "diagrams": self.diagrams,
            "warnings": self.warnings,
            "source_main": self.source_main,
            "documentclass": self.documentclass,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        blocks = [Block(**b) for b in d["blocks"]]
        math = {k: MathItem(**v) for k, v in d["math"].items()}
        return cls(
            paper_id=d["paper_id"], title=d["title"], authors=d["authors"], blocks=blocks, math=math,
            labels=d["labels"], macros=d["macros"], theorems=d["theorems"], bibliography=d["bibliography"],
            diagrams=d.get("diagrams", []), warnings=d.get("warnings", []), source_main=d.get("source_main", ""),
            documentclass=d.get("documentclass"),
        )

    def block_by_id(self, bid: str) -> Optional[Block]:
        for b in self.blocks:
            if b.id == bid:
                return b
        return None

    def block_index(self, bid: str) -> int:
        for i, b in enumerate(self.blocks):
            if b.id == bid:
                return i
        return -1
