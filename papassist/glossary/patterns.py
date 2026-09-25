"""Deterministic extraction of symbol meanings from the paper's own sentences.

Everything here is exact: a meaning is always a phrase the paper itself wrote
next to the symbol. Precision matters more than recall; the LLM pass (optional)
fills the gaps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..ingest.document import Block, Document, MathItem
from ..render.mathunits import analyze_math, normalize_tex, tokenize
from .appositive import appositive_candidates
from .textutil import PH, PH_RE, split_sentences


@dataclass
class SymbolEntry:
    key: str                     # canonical TeX key
    tex: str                     # TeX as written
    meaning: str                 # plain text with $math$
    source: str                  # paper_pattern | paper_macro | dictionary | llm
    confidence: str              # high | medium | low
    defined_at: Optional[str]    # block id
    scope: dict                  # {"kind": "paper"} or {"kind": "blocks", "blocks": [...]}
    quote: str = ""              # the sentence the meaning came from
    category: str = ""
    keys: list[str] = field(default_factory=list)   # extra lookup keys (e.g. the bare unit inside a decorated expression)
    pattern: str = ""
    math_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# What counts as "a symbol" in a formula
# ---------------------------------------------------------------------------

RELATION_CS = {"\\le", "\\leq", "\\ge", "\\geq", "\\in", "\\notin", "\\subseteq", "\\subset", "\\supseteq", "\\supset",
               "\\to", "\\rightarrow", "\\longrightarrow", "\\mapsto", "\\cong", "\\simeq", "\\sim", "\\equiv", "\\ne", "\\neq",
               "\\colon", "\\coloneqq", "\\approx", "\\propto", "\\twoheadrightarrow", "\\hookrightarrow", "\\iff", "\\implies", "\\Rightarrow"}


def top_level_split(tex: str) -> Optional[tuple[str, str, str]]:
    """Split ``tex`` at the first top-level ``=``, ``:=``, ``\\coloneqq`` or ``\\colon``/``:``.

    Returns (lhs, relation, rhs) or None.
    """
    depth = 0
    i = 0
    n = len(tex)
    while i < n:
        ch = tex[i]
        if ch == "\\":
            m = re.match(r"\\[A-Za-z]+|\\.", tex[i:])
            cs = m.group(0) if m else "\\"
            if depth == 0 and cs in ("\\coloneqq", "\\colon", "\\triangleq", "\\equiv", "\\to", "\\rightarrow", "\\longrightarrow"):
                return tex[:i], cs, tex[i + len(cs):]
            i += len(cs)
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif depth == 0 and ch == ":" and i + 1 < n and tex[i + 1] == "=":
            return tex[:i], ":=", tex[i + 2:]
        elif depth == 0 and ch == "=":
            return tex[:i], "=", tex[i + 1:]
        elif depth == 0 and ch == ":" :
            return tex[:i], ":", tex[i + 1:]
        i += 1
    return None


def split_top_commas(tex: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in tex:
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def has_relation(tex: str) -> bool:
    """True when a relation symbol occurs at brace depth 0 (scripts don't count)."""
    depth = 0
    for t in tokenize(tex):
        if t.kind == "lbrace":
            depth += 1
        elif t.kind == "rbrace":
            depth -= 1
        elif depth == 0 and t.kind == "cs" and t.text in RELATION_CS:
            return True
        elif depth == 0 and t.kind == "char" and t.text in "=<>:":
            return True
    return False


@dataclass
class SymbolShape:
    key: str
    tex: str
    extra_keys: list[str]


def symbol_shape(tex: str) -> Optional[SymbolShape]:
    """If ``tex`` denotes a single named thing, return its keys.

    Accepts a lone unit (``M_t``), a unit applied to arguments
    (``\\mathbb D^q_{i,b}(f)``, ``\\operatorname{lk}_\\Delta(i)``), a unit in
    parentheses with an outer script (``(\\Delta^t_f)_{t}``), or a very short
    relation-free expression. Set braces ``\\{i\\}`` and brackets ``k[\\Delta]``
    denote different objects and are *not* treated as decorations of the inner
    symbol.
    """
    tex = tex.strip()
    if not tex or len(tex) > 60:
        return None
    # a structure written as a tuple, (P, \le) or (M, \varphi): the first component names it
    tm = re.fullmatch(r"\s*(?:\\left|\\big|\\Big|\\bigl|\\Bigl)?\s*\((.*)\)\s*(?:[_^]\{(?:[^{}]|\{[^{}]*\})*\}|[_^]\S)*\s*", tex, re.S)
    if tm and "," in tm.group(1):
        first = split_top_commas(tm.group(1))[0]
        if not has_relation(first):
            head = symbol_shape(first)
            if head is not None:
                return SymbolShape(normalize_tex(tex), tex, [head.key, *head.extra_keys])
    if has_relation(tex):
        return None
    a = analyze_math(tex)
    tops = [u for u in a.units if u.parent is None]
    if not tops:
        return None
    key = normalize_tex(tex)
    head = tops[0]
    lead = tex[: head.start]
    if len(tops) == 1 and re.sub(r"\s", "", head.tex) == re.sub(r"\s", "", tex):
        return SymbolShape(head.key, tex, [])
    # X(args), X(args)(more), X[..] is NOT an alias of X, so only parentheses
    if not lead.strip():
        rest = tex[head.end :]
        if re.fullmatch(r"(\s*(?:\\(?:left|big|Big|bigg|Bigg|bigl|Bigl|biggl|Biggl)\s*)?\((?:[^()]|\([^()]*\))*\)(?:\s*\\(?:right|big|Big|bigg|Bigg|bigr|Bigr|biggr|Biggr))?\s*)+", rest, re.S):
            return SymbolShape(key, tex, [head.key])
    # (X)_{...} or (X)^{...}: a unit in parentheses with an outer script (units inside the script are ignored)
    if re.fullmatch(r"\s*(?:\\left|\\big|\\Big|\\bigl|\\Bigl)?\s*\(\s*", lead) and re.fullmatch(
        r"\s*(?:\\right|\\big|\\Big|\\bigr|\\Bigr)?\s*\)(?:[_^]\{(?:[^{}]|\{[^{}]*\})*\}|[_^][A-Za-z0-9]|[_^]\\[A-Za-z]+)*\s*", tex[head.end :], re.S
    ):
        return SymbolShape(key, tex, [head.key])
    # short expressions like `x_i^{c}\cdot k[...]`: accept only when tiny
    if len(tex) <= 12 and len(tops) <= 2:
        return SymbolShape(key, tex, [])
    return None


# ---------------------------------------------------------------------------
# Sentence patterns
# ---------------------------------------------------------------------------

ART = r"(?:a|an|the|any|some|its|our|their|this|that|each|every|one|two|three|finitely many|arbitrary)"
PHS = rf"{PH}(?:\s*,\s*{PH})*(?:\s*,?\s*(?:and|or)\s+{PH})?"
STOP = r"(?=\s*(?:[.;!?]|,\s*(?:and|or|where|with|whose|which|so that|such that|for|if|then|as|by|we|it|this|these|in particular|that is|i\.e\.|e\.g\.)\b|\s+(?:where|whose|which|so that|such that|indexed by|equipped with|together with|endowed with|whenever|if and only if|iff|provided|unless|for all|for each|for every|for some|defined|given by|obtained by|introduced|considered|studied)\b|\s+(?:and|or)\s+⟦|$))"
PHRASE = rf"(?P<meaning>[^.;!?⟦]*?(?:{PH}[^.;!?⟦]*?){{0,3}})"

TRIGGERS = r"(?:[Ll]et|[Ff]ix|[Cc]onsider|[Tt]ake|[Gg]iven|[Ss]uppose|[Aa]ssume|[Cc]hoose|[Pp]ick|[Ss]et|[Pp]ut|[Dd]efine|[Dd]enote|[Ww]rite|[Ll]et us write|[Ww]e write|[Ww]e set|[Ww]e put|[Ww]e define|[Ww]e let|[Ff]or|[Hh]ere)"

PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Let X be a/the ...        Fix a vertex i  (handled by appositive)   Let X and Y be the ...
    ("let_be", re.compile(rf"{TRIGGERS}\b(?:[^.;⟦]{{0,40}}?)(?P<syms>{PHS})\s*,?\s*(?:be|denote|denotes|stand for|stands for|is|are|being)\s+(?:{ART}\s+)?{PHRASE}{STOP}"), "high"),
    # and X the ...  (elided 'be' in a coordinated Let-sentence)
    ("let_elided", re.compile(rf"\b(?:and|with|,)\s+(?P<syms>{PHS})\s+(?:the|a|an)\s+{PHRASE}{STOP}"), "medium"),
    # denote(d) by X the ...
    ("denote_by", re.compile(rf"\b[Dd]enot(?:e|ed|es|ing)\s+by\s+(?P<syms>{PHS})\s+(?:the|a|an)?\s*{PHRASE}{STOP}"), "high"),
    # X denotes / stands for / is / are the ...
    ("x_denotes", re.compile(rf"(?<![\w⟧])(?P<syms>{PHS})\s+(?:denotes?|stands? for|will denote|is called|is|are)\s+{ART}\s+{PHRASE}{STOP}"), "medium"),
    # write X for the ...
    ("write_for", re.compile(rf"\b[Ww]rit(?:e|es|ing|ten)\s+(?P<syms>{PHS})\s+for\s+(?:the|a|an|its)?\s*{PHRASE}{STOP}"), "high"),
    # where X is/are/denotes the ...
    ("where_is", re.compile(rf"\b(?:[Ww]here|[Hh]ere|[Ww]ith|[Ii]n which)\s+(?P<syms>{PHS})\s+(?:is|are|denotes?|stands? for|being)\s+{ART}?\s*{PHRASE}{STOP}"), "high"),
    # for all/every x in X ; Let x \in X
    ("element_of", re.compile(rf"(?P<syms>{PH})"), "structural"),
]

APPOSITIVE_STOP_LAST = {"following", "case", "same", "corresponding", "other", "above", "below", "previous", "next", "former",
                        "latter", "first", "second", "third", "last", "usual", "standard", "given", "resulting", "associated",
                        "induced", "natural", "canonical", "obvious", "of", "in", "on", "by", "to", "for", "with", "at", "from",
                        "and", "or", "as", "than", "that", "which", "where", "is", "are", "be", "let", "fix", "if", "then",
                        "so", "all", "any", "some", "each", "every", "one", "two", "both", "either", "neither", "no", "not",
                        "denoted", "written", "called", "defined", "map", "maps", "value", "element", "elements", "number", "condition"}
APPOSITIVE_OK_LAST = {"map", "maps", "element", "elements", "number", "numbers", "vertex", "vertices", "degree", "face", "faces",
                      "ideal", "ideals", "ring", "rings", "module", "modules", "complex", "complexes", "field", "fields", "space",
                      "spaces", "functor", "functors", "filtration", "filtrations", "parameter", "parameters", "prime", "primes",
                      "set", "sets", "sequence", "sequences", "function", "functions", "morphism", "morphisms", "point", "points",
                      "index", "indices", "integer", "integers", "constant", "constants", "variable", "variables", "matrix",
                      "matrices", "graph", "graphs", "subcomplex", "subcomplexes", "category", "categories", "group", "groups",
                      "homomorphism", "homomorphisms", "isomorphism", "isomorphisms", "object", "objects", "subset", "subsets",
                      "multidegree", "multidegrees", "barcode", "barcodes", "interval", "intervals", "poset", "posets", "diagram",
                      "diagrams", "resolution", "resolutions", "distance", "distances", "metric", "metrics", "operator", "operators",
                      "transformation", "transformations", "cover", "covers", "simplex", "simplices", "cone", "cones", "monomial",
                      "monomials", "polynomial", "polynomials", "grading", "gradings", "basis", "bases", "coefficient", "coefficients",
                      "quotient", "quotients", "surjection", "surjections", "injection", "injections", "inclusion", "inclusions",
                      "subspace", "subspaces", "submodule", "submodules", "algebra", "algebras", "scheme", "schemes", "variety",
                      "varieties", "manifold", "manifolds", "bundle", "bundles", "sheaf", "sheaves", "measure", "measures", "vector",
                      "vectors", "form", "forms", "structure", "structures", "invariant", "invariants", "dimension", "dimensions",
                      "family", "families", "collection", "collections", "pair", "pairs", "tuple", "tuples", "word", "words", "path", "paths",
                      "cycle", "cycles", "chain", "chains", "boundary", "boundaries", "component", "components", "summand", "summands",
                      "factor", "factors", "term", "terms", "shift", "shifts", "weight", "weights", "threshold", "thresholds", "level", "levels",
                      "scale", "scales", "time", "times", "step", "steps", "stage", "stages", "position", "positions", "direction", "directions",
                      "homology", "cohomology", "torsion", "support", "rank", "length", "height", "depth", "radius", "diameter", "norm", "order", "type"}

SCOPED_KINDS = {"theorem", "lemma", "proposition", "corollary", "claim", "fact", "conjecture", "question", "problem", "remark",
                "example", "assumption", "proof"}


def _block_scope(doc: Document, idx: int) -> dict:
    """Scope for symbols introduced in block ``idx``."""
    b = doc.blocks[idx]
    if b.kind in ("theorem", "proof") and (b.thm_kind in SCOPED_KINDS):
        ids = [b.id]
        # a theorem's hypotheses hold in its proof (the next proof block, possibly after a remark)
        if b.kind == "theorem":
            for j in range(idx + 1, min(idx + 4, len(doc.blocks))):
                nb = doc.blocks[j]
                if nb.kind == "proof":
                    ids.append(nb.id)
                    break
                if nb.kind in ("heading", "theorem"):
                    break
        return {"kind": "blocks", "blocks": ids}
    return {"kind": "paper"}


def _clean_meaning(s: str) -> str:
    s = s.strip().strip(",;:").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _sentence_text(doc: Document, block: Block, start: int, end: int) -> str:
    s = block.text_ph[start:end]
    return PH_RE.sub(lambda m: "$" + doc.math[m.group(1)].tex + "$" if m.group(1) in doc.math else "", s).strip()


def _placeholders(s: str) -> list[str]:
    return PH_RE.findall(s)


def _meaning_to_text(doc: Document, meaning_ph: str) -> str:
    return _clean_meaning(PH_RE.sub(lambda m: "$" + doc.math[m.group(1)].tex + "$" if m.group(1) in doc.math else "", meaning_ph))


def extract_symbols(doc: Document) -> list[SymbolEntry]:
    entries: list[SymbolEntry] = []
    for idx, block in enumerate(doc.blocks):
        if block.kind in ("title", "heading"):
            continue
        scope = _block_scope(doc, idx)
        text = block.text_ph
        for s, e in split_sentences(text):
            sent = text[s:e]
            if "⟦" not in sent:
                continue
            quote = _sentence_text(doc, block, s, e)
            seen_in_sentence: set[str] = set()
            for name, pat, conf in PATTERNS:
                if name == "element_of":
                    continue
                for m in pat.finditer(sent):
                    syms = _placeholders(m.group("syms"))
                    verb = m.group(0)[len(m.group("syms")) if False else 0:]
                    if len(syms) > 1 and re.search(r"\u27e7\s*,?\s*(?:is|denotes|stands for|will denote|is called)\s", m.group(0)):
                        syms = syms[-1:]
                    meaning_ph = m.group("meaning")
                    meaning = _meaning_to_text(doc, meaning_ph)
                    if not meaning or len(meaning) < 3:
                        continue
                    if name != "where_is" and re.match(r"^(such that|so that|as follows|the following|as in|as above|as before|given by|defined|as usual|arbitrary|fixed|nonzero|zero|positive|negative|finite|infinite)\b", meaning, re.I):
                        continue
                    for mid in syms:
                        if mid in seen_in_sentence and name in ("appositive", "let_elided", "x_denotes"):
                            continue
                        item = doc.math.get(mid)
                        if item is None:
                            continue
                        shape = symbol_shape(item.tex)
                        tex_for_key = item.tex
                        rel_note = ""
                        if shape is None:
                            # maybe an equation X = ... : take the left-hand side
                            split = top_level_split(item.tex)
                            if split is None:
                                continue
                            lhs, rel, rhs = split
                            shape = symbol_shape(lhs)
                            if shape is None:
                                continue
                            if rel in ("\\colon", ":", "\\to", "\\rightarrow", "\\longrightarrow"):
                                rel_note = f"; a map ${rhs.strip()}$" if rel in ("\\colon", ":") else ""
                            elif rel in ("=", ":=", "\\coloneqq", "\\triangleq"):
                                rel_note = f"; given by ${lhs.strip()} = {rhs.strip()}$" if rel == "=" else f"; defined as ${rhs.strip()}$"
                            tex_for_key = lhs.strip()
                        plural = len(syms) > 1
                        final_meaning = meaning
                        if plural and re.match(r"^(maps|morphisms|functors|modules|complexes|numbers|elements|vertices|faces|sets|ideals|rings|spaces|sequences|functions|invariants|families)\b", meaning.lower()):
                            final_meaning = "one of the " + meaning
                        final_meaning = final_meaning + rel_note
                        seen_in_sentence.add(mid)
                        entries.append(SymbolEntry(
                            key=shape.key, tex=tex_for_key, meaning=final_meaning, source="paper_pattern", confidence=conf,
                            defined_at=block.id, scope=scope, quote=quote, keys=shape.extra_keys, pattern=name, math_id=mid,
                        ))
            # appositives: "the vertex $i$", "a degree $q$"
            for mid, phrase in appositive_candidates(sent):
                if mid in seen_in_sentence:
                    continue
                item = doc.math.get(mid)
                if item is None:
                    continue
                shape = symbol_shape(item.tex)
                rel_note = ""
                tex_for_key = item.tex
                if shape is None:
                    split = top_level_split(item.tex)
                    if split is None:
                        continue
                    lhs, rel, rhs = split
                    shape = symbol_shape(lhs)
                    if shape is None:
                        continue
                    rhs = cut_rhs(rhs)
                    rel_note = f"; given by ${lhs.strip()} = {rhs}$" if rel == "=" else (f"; defined as ${rhs}$" if rel in (":=", "\\coloneqq") else "")
                    tex_for_key = lhs.strip()
                seen_in_sentence.add(mid)
                entries.append(SymbolEntry(key=shape.key, tex=tex_for_key, meaning=phrase + rel_note, source="paper_pattern", confidence="low",
                                           defined_at=block.id, scope=scope, quote=quote, keys=shape.extra_keys, pattern="appositive", math_id=mid))
            # structural patterns: x \in X, f\colon X \to Y, X := ...
            for m in PH_RE.finditer(sent):
                mid = m.group(1)
                item = doc.math.get(mid)
                if item is None or mid in seen_in_sentence:
                    continue
                for piece in split_pieces(item.tex):
                    st = structural_entry(doc, item, block, scope, quote, piece)
                    if st is not None:
                        entries.append(st)
                        seen_in_sentence.add(mid)
    return entries


def cut_rhs(rhs: str) -> str:
    """Trim a right-hand side at the next top-level separator (\\qquad, \\quad, ;)."""
    depth = 0
    i = 0
    while i < len(rhs):
        ch = rhs[i]
        if ch == "\\":
            m = re.match(r"\\[A-Za-z]+|\\.", rhs[i:])
            cs = m.group(0) if m else "\\"
            if depth == 0 and cs in ("\\qquad", "\\quad", "\\text", "\\mbox", "\\\\"):
                return rhs[:i].strip().rstrip(",.;").strip()
            i += len(cs)
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif depth == 0 and ch == ";":
            return rhs[:i].strip().rstrip(",.").strip()
        i += 1
    return rhs.strip().rstrip(",.;").strip()


def split_pieces(tex: str) -> list[str]:
    """Split a (display) formula into independent statements: rows, \\qquad-separated parts."""
    t = re.sub(r"\\begin\{[a-z*]+\}(\{[^}]*\})?|\\end\{[a-z*]+\}", " ", tex)
    t = t.replace("&", " ")
    pieces: list[str] = []
    depth = 0
    cur = []
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == "\\":
            m = re.match(r"\\[A-Za-z]+|\\.", t[i:])
            cs = m.group(0) if m else "\\"
            if depth == 0 and cs in ("\\qquad", "\\quad", "\\\\"):
                pieces.append("".join(cur))
                cur = []
                i += len(cs)
                continue
            cur.append(cs)
            i += len(cs)
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        cur.append(ch)
        i += 1
    pieces.append("".join(cur))
    out = [p.strip().rstrip(",.;").strip() for p in pieces]
    out = [p for p in out if p and not re.fullmatch(r"\\text\{[^}]*\}", p)]
    return out or [tex]


def structural_entry(doc: Document, item: MathItem, block: Block, scope: dict, quote: str, piece: Optional[str] = None) -> Optional[SymbolEntry]:
    tex = (piece if piece is not None else item.tex).strip()
    # x \in X   (a single unit on the left of a top-level \in)
    m = re.match(r"^(.*?)\\in\b(.*)$", tex)
    if m and not has_relation(m.group(1)) and m.group(1).strip():
        lhs2 = symbol_shape(m.group(1))
        rhs2 = m.group(2).strip().rstrip(",.;")
        if lhs2 is not None and rhs2 and not has_relation(rhs2) and len(rhs2) <= 40:
            return SymbolEntry(key=lhs2.key, tex=m.group(1).strip(), meaning=f"an element of ${rhs2}$", source="paper_pattern",
                               confidence="medium", defined_at=block.id, scope=scope, quote=quote, keys=lhs2.extra_keys, pattern="element_of", math_id=item.id)
    split = top_level_split(tex)
    if split is None:
        return None
    lhs, rel, rhs = split
    lhs_shape = symbol_shape(lhs)
    if lhs_shape is None:
        return None
    rhs = cut_rhs(rhs)
    if not rhs:
        return None
    if rel in ("\\colon", ":") and ("\\to" in rhs or "\\rightarrow" in rhs or "\\longrightarrow" in rhs):
        return SymbolEntry(key=lhs_shape.key, tex=lhs.strip(), meaning=f"a map ${rhs}$", source="paper_pattern", confidence="medium",
                           defined_at=block.id, scope=scope, quote=quote, keys=lhs_shape.extra_keys, pattern="map_signature", math_id=item.id)
    if rel in (":=", "\\coloneqq", "\\triangleq"):
        return SymbolEntry(key=lhs_shape.key, tex=lhs.strip(), meaning=f"defined as ${rhs}$", source="paper_pattern", confidence="high",
                           defined_at=block.id, scope=scope, quote=quote, keys=lhs_shape.extra_keys, pattern="defined_as", math_id=item.id)
    if rel == "=" and item.display:
        # a displayed equation whose left side is a symbol: definitional in most papers
        if re.search(r"\b(define|defined|definition|let|set|put|write|denote|by|be)\b", quote.lower()) or block.thm_kind in ("definition", "construction"):
            return SymbolEntry(key=lhs_shape.key, tex=lhs.strip(), meaning=f"given by ${lhs.strip()} = {rhs}$", source="paper_pattern", confidence="medium",
                               defined_at=block.id, scope=scope, quote=quote, keys=lhs_shape.extra_keys, pattern="display_equation", math_id=item.id)
    return None


def extract_macro_symbols(doc: Document) -> list[SymbolEntry]:
    """Macros like ``\\newcommand{\\Dgm}{\\operatorname{Dgm}}`` name deliberate notation."""
    out: list[SymbolEntry] = []
    for name, mac in doc.macros.items():
        if mac.get("nargs"):
            continue
        body = mac.get("body", "").strip()
        if not body or len(body) > 60:
            continue
        shape = symbol_shape(body)
        if shape is None:
            continue
        hint = f"Notation produced by the paper's macro \\{name}."
        out.append(SymbolEntry(key=shape.key, tex=body, meaning=hint, source="paper_macro", confidence="low", defined_at=None,
                               scope={"kind": "paper"}, quote=f"\\newcommand{{\\{name}}}{{{body}}}", keys=shape.extra_keys, pattern="macro"))
    return out
