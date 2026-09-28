"""Convert pandoc's JSON syntax tree into PapAssist's document model.

pandoc gives us structure (sections, theorem-like environments, formulas,
citations, cross-references). We add what it does not do for us: theorem and
equation numbering that follows the paper's ``\\newtheorem`` declarations,
resolved ``\\ref`` texts, hover tags inside every formula, and a plain-text
view of each block for the glossary builder and the LLM.
"""
from __future__ import annotations

import copy
import html as htmllib
import re
from typing import Any, Optional

from .bib import BibEntry
from .citations import bib_anchor, bibliography_block_html, compute_labels, label_mode
from .document import Block, Document, MathItem
from .mathenv import prepare_display_math
from .preamble import Preamble, TheoremDecl
from ..render.mathunits import analyze_math

PH_OPEN, PH_CLOSE = "\u27e6", "\u27e7"    # ⟦ ⟧ inline formula placeholder
DPH_OPEN, DPH_CLOSE = "\u27ea", "\u27eb"  # ⟪ ⟫ displayed formula placeholder
DIAGRAM_RE = re.compile(r"\[PAPASSIST-DIAGRAM-(\d+)\]")
REF_RE = re.compile(r"\{\{REF:([^}]*)\}\}")
CITE_RE = re.compile(r"\{\{CITE:([^}]*)\}\}")


def esc(s: str) -> str:
    return htmllib.escape(s, quote=False)


def attr_esc(s: str) -> str:
    return htmllib.escape(s, quote=True)


def label_id(label: str) -> str:
    return "lbl-" + re.sub(r"[^A-Za-z0-9:_.\-]", "_", label)


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------

CHAPTER_CLASSES = {"book", "report", "memoir", "scrbook", "scrreprt", "amsbook"}


class Numbering:
    def __init__(self, pre: Preamble):
        self.pre = pre
        self.chapter_based = (pre.documentclass or "article") in CHAPTER_CLASSES
        self.names = (
            ["chapter", "section", "subsection", "subsubsection", "paragraph", "subparagraph"]
            if self.chapter_based
            else ["section", "subsection", "subsubsection", "paragraph", "subparagraph", "subsubparagraph"]
        )
        self.sec = [0] * len(self.names)
        self.thm: dict[str, int] = {}
        self.eq = 0
        self.fig = 0
        self.tab = 0

    def level_of(self, name: Optional[str]) -> Optional[int]:
        if name in self.names:
            return self.names.index(name) + 1
        return None

    def enter_header(self, level: int, numbered: bool) -> Optional[str]:
        name = self.names[level - 1] if level - 1 < len(self.names) else None
        if not numbered or name is None or name in ("paragraph", "subparagraph", "subsubparagraph"):
            return None
        self.sec[level - 1] += 1
        for l in range(level, len(self.sec)):
            self.sec[l] = 0
        # counters reset by this level or by any deeper level start over
        for decl in self.pre.theorems.values():
            lvl = self.level_of(decl.reset_by)
            if lvl is not None and lvl >= level:
                self.thm[decl.counter] = 0
        lvl = self.level_of(self.pre.equation_reset)
        if lvl is not None and lvl >= level:
            self.eq = 0
        return ".".join(str(self.sec[l]) for l in range(level))

    def prefix_for(self, reset_by: Optional[str]) -> str:
        lvl = self.level_of(reset_by)
        if lvl is None:
            return ""
        return ".".join(str(self.sec[l]) for l in range(lvl)) + "."

    def next_theorem(self, decl: TheoremDecl) -> str:
        self.thm[decl.counter] = self.thm.get(decl.counter, 0) + 1
        return self.prefix_for(decl.reset_by) + str(self.thm[decl.counter])

    def next_equation(self) -> str:
        self.eq += 1
        return self.prefix_for(self.pre.equation_reset) + str(self.eq)

    def next_figure(self) -> str:
        self.fig += 1
        return str(self.fig)

    def next_table(self) -> str:
        self.tab += 1
        return str(self.tab)

    @property
    def current_section(self) -> str:
        top = 0
        for i in range(len(self.sec)):
            if self.sec[i] == 0:
                break
            top = i + 1
        return ".".join(str(self.sec[l]) for l in range(top))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class _Ctx:
    """Per-block collection of what we saw while rendering."""

    def __init__(self) -> None:
        self.math_ids: list[str] = []
        self.cites: list[dict] = []
        self.refs: list[str] = []
        self.emph: list[str] = []
        self.strong: list[str] = []


def _plain(inlines: list) -> str:
    out = []
    for i in inlines:
        t = i.get("t")
        c = i.get("c")
        if t == "Str":
            out.append(c)
        elif t in ("Space", "SoftBreak", "LineBreak"):
            out.append(" ")
        elif t == "Math":
            out.append("$" + c[1] + "$")
        elif t in ("Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps", "Underline"):
            out.append(_plain(c))
        elif t in ("Span", "Link"):
            out.append(_plain(c[1]))
        elif t == "Quoted":
            out.append(_plain(c[1]))
        elif t == "Code":
            out.append(c[1])
        elif t == "Cite":
            out.append(_plain(c[1]))
    return "".join(out)


class DocBuilder:
    def __init__(
        self,
        ast: dict,
        pre: Preamble,
        bib: dict[str, BibEntry],
        paper_id: str,
        diagrams: Optional[list[str]] = None,
        source_main: str = "",
    ):
        self.ast = ast
        self.pre = pre
        self.bib = bib
        self.paper_id = paper_id
        self.diagrams = diagrams or []
        self.source_main = source_main
        self.numbering = Numbering(pre)
        self.blocks: list[Block] = []
        self.math: dict[str, MathItem] = {}
        self.labels: dict[str, dict] = {}
        self.warnings: list[str] = []
        self._bid = 0
        self._mid = 0
        self._uid = 1
        self.title = ""
        self.authors: list[str] = []
        self.cited_keys: list[str] = []
        self.cite_labels: dict[str, str] = {}
        self.cite_mode = label_mode(pre.bib_style, pre.biblatex_style)

    # -- ids ---------------------------------------------------------------
    def new_bid(self) -> str:
        self._bid += 1
        return f"b{self._bid}"

    def new_mid(self) -> str:
        self._mid += 1
        return f"m{self._mid}"

    # -- entry -------------------------------------------------------------
    def build(self) -> Document:
        meta = self.ast.get("meta", {})
        self._handle_meta(meta)
        for b in self.ast.get("blocks", []):
            self._handle_top_block(b)
        self._finish_citations()
        self._resolve_refs()
        return Document(
            paper_id=self.paper_id,
            title=self.title,
            authors=self.authors,
            blocks=self.blocks,
            math=self.math,
            labels=self.labels,
            macros={k: {"nargs": m.nargs, "body": m.body, "kind": m.kind, "default": m.default} for k, m in self.pre.macros.items()},
            theorems={env: {"title": d.title, "kind": d.kind, "counter": d.counter, "reset_by": d.reset_by, "numbered": d.numbered, "style": d.style} for env, d in self.pre.theorems.items()},
            bibliography={k: v.to_dict() for k, v in self.bib.items()},
            diagrams=self.diagrams,
            warnings=self.warnings,
            source_main=self.source_main,
            documentclass=self.pre.documentclass,
            cite_labels=self.cite_labels,
        )

    # -- meta ------------------------------------------------------------------
    def _meta_inlines(self, m: Any) -> list:
        if not m:
            return []
        t = m.get("t")
        if t == "MetaInlines":
            return m["c"]
        if t == "MetaString":
            return [{"t": "Str", "c": m["c"]}]
        if t == "MetaBlocks":
            out = []
            for b in m["c"]:
                if b.get("t") in ("Para", "Plain"):
                    out.extend(b["c"])
            return out
        return []

    def _handle_meta(self, meta: dict) -> None:
        title_inl = self._meta_inlines(meta.get("title"))
        authors = meta.get("author")
        author_names: list[str] = []
        if authors:
            items = authors["c"] if authors.get("t") == "MetaList" else [authors]
            for a in items:
                inl = [x for x in self._meta_inlines(a) if x.get("t") != "Note"]
                name = re.sub(r"\s+", " ", _plain(inl)).strip()
                if name:
                    author_names.append(name)
        self.authors = author_names
        if title_inl:
            ctx = _Ctx()
            h, t, tp = self._render_inlines(title_inl, ctx)
            self.title = t.strip()
            bid = self.new_bid()
            authors_html = esc(", ".join(author_names))
            self.blocks.append(Block(
                id=bid, kind="title", html=f"<h1 class=\"pa-title\">{h}</h1>" + (f"<p class=\"pa-authors\">{authors_html}</p>" if authors_html else ""),
                text=t.strip() + ("\n" + ", ".join(author_names) if author_names else ""), text_ph=tp.strip(),
                section="", math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
            ))
        abstract = meta.get("abstract")
        if abstract:
            ctx = _Ctx()
            blocks = abstract["c"] if abstract.get("t") == "MetaBlocks" else [{"t": "Para", "c": self._meta_inlines(abstract)}]
            h, t, tp = self._render_blocks(blocks, ctx)
            bid = self.new_bid()
            self.blocks.append(Block(
                id=bid, kind="abstract", html="<h2 class=\"pa-abstract-head\">Abstract</h2>" + h,
                text="Abstract. " + t, text_ph="Abstract. " + tp, section="",
                math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
            ))

    # -- top-level blocks ------------------------------------------------------
    def _handle_top_block(self, b: dict) -> None:
        t = b.get("t")
        c = b.get("c")
        if t == "Header":
            self._handle_header(c)
            return
        if t == "Div":
            attr, blocks = c
            ident, classes, kv = attr
            env = next((cl for cl in classes if cl in self.pre.theorems), None)
            if env is not None:
                self._handle_theorem(env, ident, blocks)
                return
        if t == "Figure":
            self._handle_figure(c)
            return
        if t == "Table":
            self._handle_table(b)
            return
        ctx = _Ctx()
        h, txt, tp = self._render_block(b, ctx)
        if not h.strip() and not txt.strip():
            return
        kind = "list" if t in ("OrderedList", "BulletList", "DefinitionList") else "para"
        bid = self.new_bid()
        self.blocks.append(Block(
            id=bid, kind=kind, html=h, text=txt.strip(), text_ph=tp.strip(), section=self.numbering.current_section,
            math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
        ))
        self._register_math_blocks(bid, ctx)

    def _register_math_blocks(self, bid: str, ctx: _Ctx) -> None:
        for mid in ctx.math_ids:
            self.math[mid].block = bid

    def _handle_header(self, c: list) -> None:
        level, attr, inlines = c
        ident, classes, kv = attr
        numbered = "unnumbered" not in classes
        number = self.numbering.enter_header(level, numbered)
        ctx = _Ctx()
        h, txt, tp = self._render_inlines(inlines, ctx)
        bid = self.new_bid()
        tag = f"h{min(level + 1, 6)}"
        num_html = f"<span class=\"pa-secnum\">{esc(number)}</span> " if number else ""
        anchor = f" id=\"{attr_esc(label_id(ident))}\"" if ident else ""
        html_ = f"<{tag} class=\"pa-heading pa-h{level}\"{anchor}>{num_html}{h}</{tag}>"
        label = ident or None
        self.blocks.append(Block(
            id=bid, kind="heading", html=html_, text=txt.strip(), text_ph=tp.strip(), section=self.numbering.current_section,
            number=number, label=label, level=level, math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
        ))
        self._register_math_blocks(bid, ctx)
        if ident:
            self.labels[ident] = {"kind": "section", "number": number, "block": bid, "title": txt.strip()}

    def _handle_theorem(self, env: str, ident: str, blocks: list) -> None:
        decl = self.pre.theorems[env]
        is_proof = decl.kind == "proof" or env == "proof"
        number = self.numbering.next_theorem(decl) if (decl.numbered and not is_proof) else None
        blocks = copy.deepcopy(blocks)
        title_inl: Optional[list] = None
        head_override: Optional[list] = None
        if blocks and blocks[0].get("t") in ("Para", "Plain"):
            inl = blocks[0]["c"]
            if is_proof:
                if inl and inl[0].get("t") == "Emph" and _plain(inl[0]["c"]).strip().lower().startswith("proof"):
                    head_override = inl[0]["c"]
                    inl = inl[1:]
            elif inl and inl[0].get("t") == "Strong":
                strong_text = _plain(inl[0]["c"]).strip()
                if strong_text.lower().startswith(decl.title.lower()[:4]):
                    inl = inl[1:]
                    while inl and inl[0].get("t") == "Space":
                        inl = inl[1:]
                    if inl and inl[0].get("t") == "Str" and inl[0]["c"].startswith("("):
                        depth = 0
                        title: list = []
                        k = 0
                        for k, x in enumerate(inl):
                            title.append(x)
                            if x.get("t") == "Str":
                                depth += x["c"].count("(") - x["c"].count(")")
                                if depth <= 0 and ")" in x["c"]:
                                    break
                        inl = inl[k + 1 :]
                        if title and title[0].get("t") == "Str":
                            title[0] = {"t": "Str", "c": title[0]["c"][1:]}
                        if title and title[-1].get("t") == "Str":
                            last = title[-1]["c"]
                            last = re.sub(r"\)\.?\s*$", "", last)
                            title[-1] = {"t": "Str", "c": last}
                        title_inl = [x for x in title if not (x.get("t") == "Str" and x["c"] == "")]
                    elif inl and inl[0].get("t") == "Str" and inl[0]["c"].startswith("."):
                        rest = inl[0]["c"][1:]
                        inl = ([{"t": "Str", "c": rest}] if rest else []) + inl[1:]
            while inl and inl[0].get("t") == "Space":
                inl = inl[1:]
            blocks[0] = {"t": blocks[0]["t"], "c": inl}
        ctx = _Ctx()
        title_html, title_text = "", None
        if title_inl:
            th, tt, _ = self._render_inlines(title_inl, ctx)
            title_html, title_text = th, tt.strip()
        body_html, body_text, body_tp = self._render_blocks(blocks, ctx)
        bid = self.new_bid()
        if is_proof:
            head_txt = _plain(head_override).strip() if head_override else "Proof."
            head_html = f"<span class=\"pa-thm-head pa-proof-head\"><em>{esc(head_txt)}</em></span> "
            heading_text = head_txt
        else:
            heading_text = decl.title + (f" {number}" if number else "") + (f" ({title_text})" if title_text else "")
            head_html = (
                f"<span class=\"pa-thm-head\"><strong>{esc(decl.title)}{(' ' + esc(number)) if number else ''}</strong>"
                + (f" ({title_html})" if title_html else "")
                + ".</span> "
            )
        # put the heading inside the first paragraph so it flows like in the PDF
        if body_html.startswith("<p>"):
            html_ = "<p>" + head_html + body_html[3:]
        else:
            html_ = "<p>" + head_html + "</p>" + body_html
        sep = " " if heading_text.endswith(".") else ". "
        text = heading_text + sep + body_text.strip()
        text_ph = heading_text + sep + body_tp.strip()
        self.blocks.append(Block(
            id=bid, kind="proof" if is_proof else "theorem", html=html_, text=text, text_ph=text_ph,
            section=self.numbering.current_section, env=env, thm_kind=decl.kind, thm_title=decl.title,
            number=number, label=ident or None, title=title_text, math_ids=ctx.math_ids, cites=ctx.cites,
            refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
        ))
        self._register_math_blocks(bid, ctx)
        if ident:
            self.labels[ident] = {"kind": decl.kind, "number": number, "block": bid, "title": heading_text}

    def _handle_figure(self, c: list) -> None:
        attr, caption, blocks = c
        ident, classes, kv = attr
        number = self.numbering.next_figure()
        ctx = _Ctx()
        cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else []
        cap_html, cap_text, cap_tp = self._render_blocks(cap_blocks, ctx)
        body_html, body_text, body_tp = self._render_blocks(blocks, ctx)
        if not body_html.strip():
            body_html = "<div class=\"pa-figure-missing\">[figure content not available in the source conversion]</div>"
        bid = self.new_bid()
        html_ = f"<figure class=\"pa-figure\">{body_html}<figcaption><strong>Figure {esc(number)}.</strong> {cap_html}</figcaption></figure>"
        self.blocks.append(Block(
            id=bid, kind="figure", html=html_, text=f"Figure {number}. {cap_text.strip()} {body_text.strip()}".strip(),
            text_ph=f"Figure {number}. {cap_tp.strip()} {body_tp.strip()}".strip(), section=self.numbering.current_section,
            number=number, label=ident or None, math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
        ))
        self._register_math_blocks(bid, ctx)
        if ident:
            self.labels[ident] = {"kind": "figure", "number": number, "block": bid, "title": cap_text.strip()[:80]}

    def _handle_table(self, b: dict) -> None:
        ctx = _Ctx()
        h, txt, tp = self._render_block(b, ctx)
        bid = self.new_bid()
        self.blocks.append(Block(
            id=bid, kind="table", html=h, text=txt.strip(), text_ph=tp.strip(), section=self.numbering.current_section,
            math_ids=ctx.math_ids, cites=ctx.cites, refs=ctx.refs, emph=ctx.emph, strong=ctx.strong,
        ))
        self._register_math_blocks(bid, ctx)

    # -- block rendering ---------------------------------------------------------
    def _render_blocks(self, blocks: list, ctx: _Ctx) -> tuple[str, str, str]:
        hs, ts, tps = [], [], []
        for b in blocks:
            h, t, tp = self._render_block(b, ctx)
            hs.append(h)
            ts.append(t)
            tps.append(tp)
        return "".join(hs), "\n".join(x for x in ts if x.strip()), "\n".join(x for x in tps if x.strip())

    def _render_block(self, b: dict, ctx: _Ctx) -> tuple[str, str, str]:
        t = b.get("t")
        c = b.get("c")
        if t == "Para":
            h, txt, tp = self._render_inlines(c, ctx)
            return f"<p>{h}</p>", txt, tp
        if t == "Plain":
            return self._render_inlines(c, ctx)
        if t == "Header":
            level, attr, inl = c
            h, txt, tp = self._render_inlines(inl, ctx)
            return f"<h{min(level + 1, 6)} class=\"pa-heading\">{h}</h{min(level + 1, 6)}>", txt, tp
        if t in ("OrderedList", "BulletList"):
            if t == "OrderedList":
                attrs, items = c
                start = attrs[0] if attrs else 1
                open_tag = f"<ol start=\"{start}\">" if start != 1 else "<ol>"
                close_tag = "</ol>"
            else:
                items = c
                open_tag, close_tag = "<ul>", "</ul>"
            hs, ts, tps = [], [], []
            for idx, item in enumerate(items):
                h, txt, tp = self._render_blocks(item, ctx)
                hs.append(f"<li>{h}</li>")
                marker = f"({idx + 1}) " if t == "OrderedList" else "- "
                ts.append(marker + txt)
                tps.append(marker + tp)
            return open_tag + "".join(hs) + close_tag, "\n".join(ts), "\n".join(tps)
        if t == "DefinitionList":
            hs, ts, tps = [], [], []
            for term, defs in c:
                th, tt, ttp = self._render_inlines(term, ctx)
                hs.append(f"<dt>{th}</dt>")
                ts.append(tt)
                tps.append(ttp)
                for d in defs:
                    dh, dt, dtp = self._render_blocks(d, ctx)
                    hs.append(f"<dd>{dh}</dd>")
                    ts.append(dt)
                    tps.append(dtp)
            return "<dl>" + "".join(hs) + "</dl>", "\n".join(ts), "\n".join(tps)
        if t == "BlockQuote":
            h, txt, tp = self._render_blocks(c, ctx)
            return f"<blockquote>{h}</blockquote>", txt, tp
        if t == "Div":
            attr, blocks = c
            ident, classes, kv = attr
            h, txt, tp = self._render_blocks(blocks, ctx)
            cls = " ".join("pa-" + x for x in classes) if classes else ""
            anchor = f" id=\"{attr_esc(label_id(ident))}\"" if ident else ""
            if ident:
                self.labels.setdefault(ident, {"kind": "div", "number": None, "block": None, "title": ""})
            return f"<div class=\"{cls}\"{anchor}>{h}</div>", txt, tp
        if t == "CodeBlock":
            attr, code = c
            return f"<pre class=\"pa-code\">{esc(code)}</pre>", code, code
        if t == "RawBlock":
            fmt, raw = c
            self.warnings.append(f"raw block dropped ({fmt}): {raw[:60]!r}")
            return "", "", ""
        if t == "HorizontalRule":
            return "<hr>", "", ""
        if t == "LineBlock":
            hs, ts, tps = [], [], []
            for line in c:
                h, txt, tp = self._render_inlines(line, ctx)
                hs.append(h)
                ts.append(txt)
                tps.append(tp)
            return "<p class=\"pa-lineblock\">" + "<br>".join(hs) + "</p>", "\n".join(ts), "\n".join(tps)
        if t == "Figure":
            attr, caption, blocks = c
            cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else []
            ch, ct, ctp = self._render_blocks(cap_blocks, ctx)
            bh, bt, btp = self._render_blocks(blocks, ctx)
            return f"<figure class=\"pa-figure\">{bh}<figcaption>{ch}</figcaption></figure>", ct + " " + bt, ctp + " " + btp
        if t == "Table":
            return self._render_table(c, ctx)
        if t == "Null":
            return "", "", ""
        self.warnings.append(f"unhandled block type {t}")
        return "", "", ""

    def _render_table(self, c: list, ctx: _Ctx) -> tuple[str, str, str]:
        try:
            attr, caption, colspecs, head, bodies, foot = c
        except ValueError:
            return "", "", ""
        rows_html, rows_text = [], []

        def render_rows(rows: list, cell_tag: str) -> None:
            for row in rows:
                _, cells = row
                cells_html, cells_text = [], []
                for cell in cells:
                    _, _, rowspan, colspan, blocks = cell
                    h, txt, _ = self._render_blocks(blocks, ctx)
                    span = ""
                    if isinstance(colspan, int) and colspan > 1:
                        span += f" colspan=\"{colspan}\""
                    if isinstance(rowspan, int) and rowspan > 1:
                        span += f" rowspan=\"{rowspan}\""
                    cells_html.append(f"<{cell_tag}{span}>{h}</{cell_tag}>")
                    cells_text.append(txt)
                rows_html.append("<tr>" + "".join(cells_html) + "</tr>")
                rows_text.append(" | ".join(cells_text))

        render_rows(head[1] if head else [], "th")
        for body in bodies:
            render_rows(body[2], "td")
            render_rows(body[3], "td")
        render_rows(foot[1] if foot else [], "td")
        cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else []
        ch, ct, ctp = self._render_blocks(cap_blocks, ctx)
        cap = f"<caption>{ch}</caption>" if ch.strip() else ""
        text = "\n".join(rows_text) + ("\n" + ct if ct else "")
        return f"<table class=\"pa-table\">{cap}{''.join(rows_html)}</table>", text, text

    # -- inline rendering --------------------------------------------------------
    def _render_inlines(self, inlines: list, ctx: _Ctx) -> tuple[str, str, str]:
        hs: list[str] = []
        ts: list[str] = []
        tps: list[str] = []
        for i in inlines:
            h, t, tp = self._render_inline(i, ctx)
            hs.append(h)
            ts.append(t)
            tps.append(tp)
        return "".join(hs), "".join(ts), "".join(tps)

    def _render_inline(self, i: dict, ctx: _Ctx) -> tuple[str, str, str]:
        t = i.get("t")
        c = i.get("c")
        if t == "Str":
            m = DIAGRAM_RE.fullmatch(c)
            if m:
                n = int(m.group(1))
                src = self.diagrams[n - 1] if 0 < n <= len(self.diagrams) else ""
                h = (f"<details class=\"pa-diagram\"><summary>Diagram {n} (TikZ source, not rendered)</summary>"
                     f"<pre>{esc(src)}</pre></details>")
                return h, f"[diagram {n} omitted]", f"[diagram {n} omitted]"
            return esc(c), c, c
        if t in ("Space", "SoftBreak"):
            return " ", " ", " "
        if t == "LineBreak":
            return "<br>", "\n", "\n"
        if t == "Emph":
            h, txt, tp = self._render_inlines(c, ctx)
            phrase = re.sub(r"\s+", " ", txt).strip()
            if phrase and len(phrase) <= 80 and len(phrase.split()) <= 8:
                ctx.emph.append(phrase)
            return f"<em>{h}</em>", txt, tp
        if t == "Strong":
            h, txt, tp = self._render_inlines(c, ctx)
            phrase = re.sub(r"\s+", " ", txt).strip()
            if phrase and len(phrase) <= 80 and len(phrase.split()) <= 8:
                ctx.strong.append(phrase)
            return f"<strong>{h}</strong>", txt, tp
        if t in ("Strikeout", "Superscript", "Subscript", "SmallCaps", "Underline"):
            tag = {"Strikeout": "s", "Superscript": "sup", "Subscript": "sub", "SmallCaps": "span", "Underline": "u"}[t]
            h, txt, tp = self._render_inlines(c, ctx)
            cls = " class=\"pa-smallcaps\"" if t == "SmallCaps" else ""
            return f"<{tag}{cls}>{h}</{tag}>", txt, tp
        if t == "Quoted":
            qt, inl = c
            q1, q2 = ("‘", "’") if qt.get("t") == "SingleQuote" else ("“", "”")
            h, txt, tp = self._render_inlines(inl, ctx)
            return q1 + h + q2, q1 + txt + q2, q1 + tp + q2
        if t == "Code":
            attr, code = c
            return f"<code>{esc(code)}</code>", code, code
        if t == "Math":
            return self._render_math(c, ctx)
        if t == "RawInline":
            fmt, raw = c
            return self._render_raw_inline(raw)
        if t == "Cite":
            return self._render_cite(c, ctx)
        if t == "Link":
            attr, inl, target = c
            ident, classes, kv = attr
            kvd = dict(kv)
            url = target[0]
            if "reference-type" in kvd or url.startswith("#"):
                label = kvd.get("reference", url.lstrip("#"))
                rtype = kvd.get("reference-type", "ref")
                ctx.refs.append(label)
                ph = "{{REF:" + label + "}}"
                if rtype == "eqref":
                    return (f"<a class=\"pa-ref\" href=\"#{attr_esc(label_id(label))}\" data-label=\"{attr_esc(label)}\">({ph})</a>", f"({ph})", f"({ph})")
                return (f"<a class=\"pa-ref\" href=\"#{attr_esc(label_id(label))}\" data-label=\"{attr_esc(label)}\">{ph}</a>", ph, ph)
            h, txt, tp = self._render_inlines(inl, ctx)
            if not h.strip():
                h, txt, tp = esc(url), url, url
            return f"<a class=\"pa-extlink\" href=\"{attr_esc(url)}\" target=\"_blank\" rel=\"noopener\">{h}</a>", txt, tp
        if t == "Image":
            attr, alt, target = c
            src = target[0]
            alt_h, alt_t, _ = self._render_inlines(alt, ctx)
            return f"<img class=\"pa-img\" src=\"pa-asset/{attr_esc(src)}\" alt=\"{attr_esc(alt_t)}\">", f"[image: {alt_t}]", f"[image: {alt_t}]"
        if t == "Note":
            h, txt, tp = self._render_blocks(c, ctx)
            plain = re.sub(r"<[^>]+>", "", h)
            return (f"<sup class=\"pa-fn\" title=\"{attr_esc(plain.strip())}\">†</sup>", f" (footnote: {txt.strip()})", f" (footnote: {tp.strip()})")
        if t == "Span":
            attr, inl = c
            ident, classes, kv = attr
            h, txt, tp = self._render_inlines(inl, ctx)
            if ident:
                self.labels.setdefault(ident, {"kind": "span", "number": None, "block": None, "title": txt.strip()[:80]})
                return f"<span id=\"{attr_esc(label_id(ident))}\">{h}</span>", txt, tp
            return f"<span>{h}</span>", txt, tp
        self.warnings.append(f"unhandled inline type {t}")
        return "", "", ""

    def _render_raw_inline(self, raw: str) -> tuple[str, str, str]:
        r = raw.strip()
        simple = {"\\S": "§", "\\P": "¶", "\\ldots": "…", "\\dots": "…", "\\&": "&", "\\%": "%", "\\#": "#", "\\_": "_", "~": "\u00a0", "\\,": " ", "\\ ": " ", "\\@": "", "\\/": "", "\\-": ""}
        if r in simple:
            v = simple[r]
            return esc(v), v, v
        if re.fullmatch(r"\\(?:noindent|allowbreak|linebreak|newline|qedhere|clearpage|newpage|smallskip|medskip|bigskip|vfill|hfill|centering|par|indent|nobreak|relax|maketitle|tableofcontents|label\{[^}]*\})", r):
            return "", "", ""
        self.warnings.append(f"raw inline dropped: {r[:60]!r}")
        return "", "", ""

    def _render_cite(self, c: list, ctx: _Ctx) -> tuple[str, str, str]:
        citations, fallback = c
        labels_html, labels_text = [], []
        keys = []
        titles = []
        prefix_h = prefix_t = ""
        suffix_h = suffix_t = ""
        for cit in citations:
            key = cit.get("citationId", "")
            keys.append(key)
            if key not in self.cited_keys:
                self.cited_keys.append(key)
            entry = self.bib.get(key)
            pre_h, pre_t, _ = self._render_inlines(cit.get("citationPrefix", []), ctx)
            suf_h, suf_t, _ = self._render_inlines(cit.get("citationSuffix", []), ctx)
            if pre_t.strip():
                prefix_h, prefix_t = pre_h.strip(), pre_t.strip()
            if suf_t.strip():
                suffix_h, suffix_t = suf_h.strip(), suf_t.strip()
            ph = "{{CITE:" + key + "}}"
            labels_html.append(f"<a class=\"pa-cite-num\" href=\"#{attr_esc(bib_anchor(key))}\" data-key=\"{attr_esc(key)}\">{ph}</a>")
            labels_text.append(ph)
            if entry:
                titles.append(f"{entry.short}: {entry.title}" + (f". {entry.venue}" if entry.venue else ""))
            else:
                titles.append(key)
            ctx.cites.append({"key": key, "prefix": pre_t.strip(), "suffix": suf_t.strip()})
        inner_h = ", ".join(labels_html)
        inner_t = ", ".join(labels_text)
        if prefix_t:
            inner_h, inner_t = prefix_h + " " + inner_h, prefix_t + " " + inner_t
        if suffix_t:
            inner_h, inner_t = inner_h + ", " + suffix_h, inner_t + ", " + suffix_t
        title_attr = attr_esc(" | ".join(titles)) if titles else ""
        h = f"<span class=\"pa-cite\" data-keys=\"{attr_esc(','.join(keys))}\" title=\"{title_attr}\">[{inner_h}]</span>"
        t = "[" + inner_t + "]"
        return h, t, t

    def _render_math(self, c: list, ctx: _Ctx) -> tuple[str, str, str]:
        kind, tex = c
        display = kind.get("t") == "DisplayMath"
        # a drawing that sat inside a math environment
        only = re.fullmatch(r"\s*(?:\\begin\{(?:equation\*?|center|gather\*?|align\*?)\})?\s*\\mbox\{\[PAPASSIST-DIAGRAM-(\d+)\]\}\s*(?:\\end\{(?:equation\*?|center|gather\*?|align\*?)\})?\s*", tex)
        if only:
            return self._render_inline({"t": "Str", "c": f"[PAPASSIST-DIAGRAM-{only.group(1)}]"}, ctx)
        tex = re.sub(r"\\mbox\{\[PAPASSIST-DIAGRAM-(\d+)\]\}", lambda m: "\\text{[diagram " + m.group(1) + " omitted]}", tex)
        mid = self.new_mid()
        ctx.math_ids.append(mid)
        if display:
            prep = prepare_display_math(tex, self.numbering.next_equation)
            analysis = analyze_math(prep.render, self._uid)
            self._uid += len(analysis.units)
            item = MathItem(id=mid, tex=prep.tex, display=True, tagged=analysis.tagged, src=prep.render, bare=prep.bare, block="",
                            units=[u.to_dict() for u in analysis.units], labels=prep.labels, numbers=prep.numbers)
            self.math[mid] = item
            for lbl, num in prep.label_numbers.items():
                self.labels[lbl] = {"kind": "equation", "number": num, "block": None, "math": mid, "title": ""}
            for lbl in prep.labels:
                self.labels.setdefault(lbl, {"kind": "equation", "number": None, "block": None, "math": mid, "title": ""})
            anchor = f" id=\"{attr_esc(label_id(prep.labels[0]))}\"" if prep.labels else ""
            content = esc(analysis.tagged)
            h = f"<span class=\"pa-math pa-display\" data-mid=\"{mid}\"{anchor}>{content}</span>"
            t = "\n$$" + prep.tex + "$$\n"
            tp = f" {DPH_OPEN}{mid}{DPH_CLOSE} "
            return h, t, tp
        clean = re.sub(r"\\label\s*\{[^}]*\}", "", tex).strip()
        analysis = analyze_math(clean, self._uid)
        self._uid += len(analysis.units)
        item = MathItem(id=mid, tex=clean, display=False, tagged=analysis.tagged, src=clean, bare=False, block="",
                        units=[u.to_dict() for u in analysis.units])
        self.math[mid] = item
        h = f"<span class=\"pa-math\" data-mid=\"{mid}\">\\({esc(analysis.tagged)}\\)</span>"
        return h, "$" + clean + "$", f"{PH_OPEN}{mid}{PH_CLOSE}"

    # -- citations --------------------------------------------------------------
    def _finish_citations(self) -> None:
        if not self.cited_keys:
            return
        labels, order = compute_labels(self.cited_keys, self.bib, self.cite_mode)
        self.cite_labels = labels

        def sub_html(m: re.Match) -> str:
            return esc(labels.get(m.group(1), m.group(1)))

        def sub_text(m: re.Match) -> str:
            return labels.get(m.group(1), m.group(1))

        for b in self.blocks:
            if "{{CITE:" in b.html:
                b.html = CITE_RE.sub(sub_html, b.html)
            if "{{CITE:" in b.text:
                b.text = CITE_RE.sub(sub_text, b.text)
            if "{{CITE:" in b.text_ph:
                b.text_ph = CITE_RE.sub(sub_text, b.text_ph)
            if b.title and "{{CITE:" in b.title:
                b.title = CITE_RE.sub(sub_text, b.title)
        for lbl, info in self.labels.items():
            if info.get("title") and "{{CITE:" in info["title"]:
                info["title"] = CITE_RE.sub(sub_text, info["title"])
        html_, text = bibliography_block_html(order, labels, self.bib)
        bid = self.new_bid()
        self.blocks.append(Block(id=bid, kind="bibliography", html=html_, text=text, text_ph=text, section=self.numbering.current_section))

    # -- cross references -------------------------------------------------------
    def _resolve_refs(self) -> None:
        # equations know their block only now
        for mid, item in self.math.items():
            for lbl in item.labels:
                if lbl in self.labels and self.labels[lbl].get("block") is None:
                    self.labels[lbl]["block"] = item.block

        def sub(m: re.Match) -> str:
            lbl = m.group(1)
            info = self.labels.get(lbl)
            if info and info.get("number"):
                return info["number"]
            if info and info.get("kind") == "section" and info.get("title"):
                return info["title"]
            return "??"

        for b in self.blocks:
            if "{{REF:" in b.html:
                b.html = REF_RE.sub(lambda m: esc(sub(m)), b.html)
            if "{{REF:" in b.text:
                b.text = REF_RE.sub(sub, b.text)
            if "{{REF:" in b.text_ph:
                b.text_ph = REF_RE.sub(sub, b.text_ph)
