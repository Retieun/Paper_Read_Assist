# PapAssist — Plan (v0.1, 2026-09-25)

PapAssist is a reading assistant for mathematics papers. You give it a paper as
LaTeX source (or, later, as arXiv HTML). It shows the paper in your browser.
When you hover over a symbol in a formula, or over a technical term in the text,
a side panel tells you what that symbol or term means *in this paper*, where it
was introduced, and where the explanation came from. If the paper never defines
the term itself, PapAssist goes looking: first in the papers this paper cites,
then, clearly labelled, in an LLM's general knowledge or on the web.

This document is the plan for building it. **Status (2026-09-25): the Phase 1
prototype is built** (see the README for how to run it and Section 7 for what it
covers); Phases 2 and 3 are next. The decisions in Section 10 were taken as
follows: Claude via the Anthropic API; Windows first with a Mac/Linux script; the
user's own paper as the test case; hover-preview and click-to-pin; **no**
general-knowledge answers from the LLM (only answers grounded in the paper's
text); paper text may be sent to the API; Python plus a browser UI.

---

## 1. What we are building

**Inputs (in order of priority)**

1. LaTeX source: a single `.tex` file, a folder, or an arXiv source archive
   (`.tar.gz` / `.zip`). arXiv provides the source for nearly every math paper.
2. An arXiv identifier or URL (PapAssist downloads the source itself).
3. arXiv's HTML rendering of a paper (`arxiv.org/html/<id>` or `ar5iv`). This is
   produced by LaTeXML and is very well structured, so it maps onto the same
   internal model as the LaTeX path.
4. PDF: **out of scope** for now. Recovering formula structure from PDF needs
   OCR-style tools (Mathpix, Nougat) and is a project of its own. It can be
   bolted on later as "PDF → LaTeX → PapAssist".

**Behaviours**

- Hover a symbol in a formula → side panel shows its meaning in this paper.
- Hover a technical term → side panel shows its definition.
- Definitions can come from: this paper, a cited paper, a built-in dictionary of
  standard notation, or an LLM. The source is always shown.
- Anything shown in the side panel is itself hoverable and clickable, so a
  definition that uses another undefined notion can be followed down as far as
  you like.

**Non-goals for now:** PDF input, editing the paper, collaborative features,
anything cloud-hosted. PapAssist runs on your own machine.

---

## 2. What it will feel like to use

1. Double-click `papassist.bat` (Windows) or run `papassist.sh` (Mac/Linux). The
   first run creates a Python environment and installs dependencies. A browser
   tab opens at `http://localhost:8765`.
2. Drag a `.tex` file, a folder, or an arXiv source archive onto the page (or
   paste an arXiv id). PapAssist converts it and shows the paper on the left,
   with sections, numbered theorems and definitions, references, and citations,
   and an empty side panel on the right.
3. Behind the scenes, PapAssist builds a **glossary** for the paper: every
   symbol and every defined term, with where it was introduced. This takes a
   few seconds without an LLM, or under a minute with one.
4. You read. When your mouse rests on $M_t$ in a formula, the panel shows:

   > **$M_t$** — the vector space that the persistence module $M$ assigns to
   > the real number $t$; written $M_t$ for $M(t)$.
   > *Source: this paper, Definition 2.1 (jump).*
   > Also appears 41 times (highlighted).

5. When your mouse rests on the words "interleaving distance", the panel shows
   the definition from this paper if there is one. If there is not, it says so
   and shows what it found in the cited papers, e.g.:

   > **interleaving distance** — not defined in this paper. Defined in
   > Chazal, de Silva, Glisse, Oudot (2016), Definition 3.1: ... (full text,
   > rendered, hoverable).
   > *Source: cited paper [4], fetched from arXiv.*

   If no source paper is available, the panel shows an LLM explanation with a
   clear "no source; verify" label.
6. Inside that definition the word "functor" is itself hoverable. Clicking it
   pushes a new card onto the panel. A breadcrumb trail lets you go back.
7. You can pin cards. Pinned cards form a personal cheat sheet for the paper,
   exportable as Markdown.
8. Every paper you open, and every cited paper PapAssist fetches, goes into a
   local **library**. Definitions found once are reused across papers.

---

## 3. Architecture at a glance

```
 you                       your machine                                 internet (optional)
 ───                       ────────────                                 ───────────────────
 browser tab  ◄──HTTP──►  PapAssist server (Python, FastAPI)
  │ reader view              │
  │ side panel               ├─ ingest/    .tex ──pandoc──► document model (JSON)
  │ MathJax rendering        │             arXiv HTML ─────► document model (JSON)
  │                          ├─ glossary/  pattern extractors + standard dictionary
  │                          │             + LLM enrichment ──────────────────────► Claude API
  │                          ├─ resolve/   answers "what does X mean here?"
  │                          ├─ cite/      .bib/.bbl → arXiv id → fetch source ──► arxiv.org
  │                          └─ library/   per-paper folders, caches (on disk)
```

Components:

| Component | Job |
|---|---|
| **Ingest** | Turn LaTeX (via pandoc) or arXiv HTML into one internal document model: an ordered list of blocks (paragraphs, headings, theorem-like environments with their kind, number and label, equations), each block holding text runs, formulas (with their TeX), citations and cross-references. Also extracts the macro table (`\newcommand`, `\DeclareMathOperator`), the bibliography, and metadata. |
| **Renderer** | Produces the HTML the browser shows. Formulas are left as TeX for MathJax to typeset in the browser. Before that, each identifier unit in each formula is tagged so it can be hovered individually (see 4.2). Known terms in the text are wrapped so they can be hovered too. |
| **Glossary builder** | Builds the symbol table and the term index for a paper, from deterministic extraction first and LLM enrichment second. Stored on disk next to the paper. |
| **Resolver** | Given "the user hovered X at location L", decides what to show, in a fixed order of trust: this paper (scoped) → macro table → standard dictionary → cited papers → LLM from context → LLM general knowledge / web. Caches every answer. |
| **Citation follower** | Maps citation keys to real papers, finds their arXiv source, fetches and ingests them (same pipeline), and searches their glossary. |
| **Library** | Folder on disk (`library/<paper-id>/`) with the source, the document model, the glossary and the answer cache. Cited papers live there too and are shared across papers. |
| **LLM layer** | Thin wrapper around the Claude API with a handful of well-defined prompts (build glossary, explain symbol in context, find definition in this text, list prerequisite notions). Every call is cached by a hash of its inputs. Pluggable, so another provider could be added later. |

---

## 4. The pipeline, step by step

### 4.1 Ingest: LaTeX → document model

- **pandoc** does the heavy lifting. I tested it today (pandoc 3.9) on a sample
  with `\input`, `\newtheorem`, `\label`/`\ref`, `\cite` with a suffix, and
  math macros. It resolved the `\input`, produced one block per theorem-like
  environment with its kind (`definition`, `thm`, `proof`), its label and its
  number ("Definition 1 (Persistence module)"), kept citation keys and suffixes
  ("Thm. 4.4"), turned `\ref` into links, and expanded math macros. That is
  exactly the structure the glossary needs.
- pandoc ships inside the `pypandoc_binary` pip package (Windows, Mac and Linux
  wheels), so users install nothing beyond Python.
- We read pandoc's JSON syntax tree, not its HTML, and build our own model. The
  model records, for each block: kind, number, label, title, plain text, HTML,
  and the list of formulas with their TeX.
- The preamble is parsed separately (a small Python pass) for the **macro
  table**: `\newcommand{\Dgm}{\operatorname{Dgm}}` tells us that the symbol
  `Dgm` is deliberate notation and gives a hint about its meaning. pandoc
  expands macros before rendering (which is what we want for robust display), so
  we match expansions back to macro names for the glossary.
- Theorem-like environment names are read from `\newtheorem` declarations so
  `defn`, `definition`, `Def` and so on are all recognised as definitions.
- Bibliography: `.bib` files via `bibtexparser`; `.bbl` / `thebibliography`
  via a small parser. Each entry keeps title, authors, year, DOI, arXiv id,
  URL.
- Multi-file projects: we detect the main file (the one with `\documentclass`)
  and let pandoc follow `\input`/`\include`.
- Failure handling: pandoc is tolerant, but exotic packages or `\def` tricks
  can break it. Plan: a light pre-processing step (strip unknown packages,
  expand simple `\def`s), and if pandoc still fails, a degraded "text and math
  only" view built with `pylatexenc` so you can at least read and hover.

### 4.2 Rendering and hover targets

- The browser renders formulas with **MathJax 3**, vendored into the app (no
  CDN dependency, works offline).
- **Symbol-level hover.** The server tokenises each formula's TeX into
  *identifier units*: a letter or Greek letter together with its sub- and
  superscripts and accents ($M_t$, $\varphi_M$, $\hat f$, $\tilde{X}_\varepsilon$),
  a styled letter ($\mathcal{M}$, $\mathbb{R}$, $\mathbf{Vec}$), an operator
  name ($\mathrm{Dgm}$, $\operatorname{PH}$), or a named function application
  ($\mathrm{Dgm}(M)$). Each unit is wrapped in MathJax's `\class{...}{...}`
  command with a unique id. I verified today that MathJax keeps these classes on
  the rendered elements (including on operators, without changing spacing), so
  the browser knows exactly which unit is under the mouse. Units can nest
  ($M$ inside $\mathrm{Dgm}(M)$); the innermost wins and the panel shows the
  chain.
- **Operators and relations** ($\otimes$, $\to$, $\le$, $\partial$) are not
  wrapped. MathJax encodes each character's code point in the rendered element,
  so we recover the symbol from that and look it up in the standard dictionary.
- **Fallback:** hovering anywhere in a formula that has no unit under the mouse
  shows a card listing every symbol in that formula.
- **Term hover.** After the term index is built, the text is scanned and every
  occurrence of a known term (allowing plurals and hyphenation variants) is
  wrapped in a span. Terms the paper uses but never defines (found by the
  glossary builder) are wrapped too, so hovering them triggers the cited-paper
  or LLM lookup. For anything we missed, selecting a phrase and pressing a key
  looks it up.
- **Hover behaviour:** resting the mouse for ~250 ms shows a preview card;
  moving the mouse into the panel freezes the preview so you can click inside
  it; clicking a symbol or term pins the card onto the stack. All occurrences
  of the hovered symbol are highlighted in the paper.

### 4.3 Building the glossary

**Symbols** come from four sources, merged with the most specific winning:

1. *Macro table* (from the preamble): `\Dgm` → `\operatorname{Dgm}`.
2. *Pattern extraction* over the text with formulas in place. Patterns like
   "Let $X$ be …", "we denote by $\mathcal{M}$ the …", "$M_t$ for $M(t)$",
   "where $\varepsilon>0$ is …", "for some $x\in X$", "$f\colon X\to Y$ is …"
   give (symbol, meaning, location). These are cheap, exact, and never
   hallucinate.
3. *LLM pass* over the paper (optional, one call per section or per paper,
   structured JSON output). For every notation it lists: TeX, meaning, category
   (set, map, number, category, functor, index, …), the block where it is
   introduced, and its **scope** (whole paper / this section / this theorem and
   proof). Scope is what lets $i$ mean one thing in Lemma 3 and another in
   Lemma 5.
4. *Standard-notation dictionary*, a built-in JSON file: $\mathbb{R}$,
   $\otimes$, $\to$, $\circ$, $\partial$, $\varepsilon$ ("usually a small
   positive real"), plus field-specific sets (for topological data analysis:
   $d_B$, $d_I$, $\mathrm{Dgm}$, $H_k$, $\mathrm{PH}$, …). Entries are
   labelled "standard usage" and rank below anything the paper itself says.

**Terms** come from three sources:

1. *Definition environments.* Inside a `definition`-kind block, the emphasised
   phrase(s) are the defined terms ("A *persistence module* is a functor …").
   The whole block is the definition. This is the gold standard.
2. *Inline definitions* by pattern: "we say that … is *X* if …", "… is called
   a *X*", "recall that a *X* is …", "*X* (see [12])".
3. *LLM pass*: terms used but not defined in the paper, each with the
   citation(s) the sentence points to, if any ("following [4]" → key
   `chazal2016structure`). This is the list that drives cited-paper lookups.

Every glossary entry records `source` (paper / cited / dictionary / llm),
`defined_at` (block id), `scope`, and the exact quoted text where applicable.

### 4.4 Answering a hover

Resolution order for "symbol or term X hovered at block L":

1. An in-paper entry whose scope contains L (most specific scope first).
2. A global in-paper entry.
3. Macro table (for symbols).
4. Standard dictionary (for symbols).
5. Cited papers already in the library (for terms; see 4.5).
6. Cited papers not yet fetched, if the citation hints point somewhere (4.5).
7. LLM, given the surrounding paragraph and the relevant glossary entries:
   "what does X mean here?" Labelled "inferred from context".
8. LLM general knowledge, and optionally web search. Labelled "no source".

Every answer is cached on disk keyed by (paper, X, scope), so a second hover is
instant and free. The panel always shows *which* step produced the answer.

### 4.5 Definitions that live in cited papers (your problem 1)

This is the "search the cited paper" approach, and it is feasible because the
cited papers are almost always on arXiv too.

1. **Which citation?** The sentence introducing the undefined term usually
   says: "interleavings, introduced in [4]", "we follow the conventions of
   [7, §2]". Rank citations in the same sentence, then paragraph, by cue
   phrases; the LLM pass also proposes `source_cite_keys` per undefined term.
   If no hint exists, search every cited paper in the library, then offer to
   fetch the rest.
2. **Which paper is [4]?** Bibliography entry → arXiv id directly if present
   (`eprint`, `url`, `note` fields); else DOI; else title-and-author search via
   the arXiv API (Semantic Scholar / Crossref as backups).
3. **Fetch and ingest.** Download the source from `arxiv.org/e-print/<id>`,
   detect the main file, run the same ingest and deterministic glossary build.
   Now the cited paper has a term index too. If the citation carried a pointer
   like "Def. 2.1" or "Thm. 4.4", jump straight to that numbered block (pandoc
   gives us the numbering).
4. **Search.** Look the term up in the cited paper's term index (exact, then
   inflected, then the LLM asked to find the definition in the relevant
   section, with citations to the exact passage).
5. **Show.** The definition text, rendered and hoverable, with "Source: cited
   paper [4], Definition 2.1" and a link to open that paper in PapAssist.
6. **Recursion.** The cited paper's definition may itself point to *its*
   citations. We follow at most two hops automatically; beyond that the user
   clicks to continue. Everything fetched stays in the library.

When the cited work is a book or a journal paper without a preprint, we fall
back to the LLM with a clear label, and let you drop the source (`.tex`, or text)
into the library manually to fill the gap.

This is where the LLM stops being a lookup and becomes a small **agent**: given
tools `search_this_paper`, `search_library`, `fetch_cited_paper`,
`read_block`, and later `web_search`, it can decide for itself what to fetch
and read. The Anthropic SDK's tool runner handles that loop. The prototype uses
the simpler fixed pipeline above; the agent version comes in Phase 2.

### 4.6 Definitions that need definitions (your problem 2)

Three mechanisms, none of which require deciding depth in advance:

1. **Cards are documents.** The text of every card goes through the same
   renderer as the paper, so its symbols and terms are hoverable and clickable.
   You drill down by clicking; a breadcrumb trail takes you back. Depth is
   whatever you choose.
2. **Explicit prerequisites.** Each card lists "uses: functor, poset, vector
   space over $\mathbb{F}$", each clickable. The list comes from scanning the
   definition for known terms plus, if the LLM is on, its own list of the
   notions the definition relies on. When a card opens, its direct
   prerequisites are resolved in the background so the next click is instant.
   We never pre-expand deeper than one level automatically (cost control).
3. **Two reading levels per card.** *Formal*: the literal definition from the
   paper or cited paper. *Plain English*: an LLM paraphrase with an example,
   generated on request and labelled as such. This matches how you like
   explanations: plain English unless it is the mathematics itself.

Later, the same data gives a **prerequisite graph** for the paper: what you
need to know to read Section 3, in dependency order. That is Phase 3.

### 4.7 Trust labels

Mathematics is unforgiving of a confident wrong definition, so every card
carries one of these labels, and sources are never blended silently:

| Label | Meaning |
|---|---|
| This paper | Quoted from this paper, with a jump link. |
| Cited paper | Quoted from a cited paper fetched into the library, with paper and block number. |
| Standard notation | From the built-in dictionary; the paper did not redefine it. |
| Inferred from context | An LLM's reading of this paper's own text; the text it used is shown so you can check. |
| No source | LLM general knowledge or web search. Verify before relying on it. |

---

## 5. Technology choices (and why)

| Choice | Why |
|---|---|
| **Local web app**: Python server + browser UI, started by a `.bat`/`.sh` | Hover-heavy UI wants a browser; MathJax renders TeX faithfully; nothing to install beyond Python; works on Windows, Mac, Linux; no Electron build step. |
| **Python 3.11+**, FastAPI + uvicorn | Best ecosystem for the parsing side (`pypandoc_binary`, `pylatexenc`, `bibtexparser`, `beautifulsoup4`) and the official `anthropic` SDK. |
| **pandoc** (bundled via pip) for LaTeX | Verified today on theorem environments, labels, citations, `\input`, macros. Far more robust than a home-grown parser; LaTeXML would be higher fidelity but is a painful Windows install and slow. |
| **MathJax 3**, vendored | Widest LaTeX-math coverage; `\class{}` lets the server tag hover targets (verified today); works offline. |
| **Vanilla JavaScript** front end | No build tooling; the UI is one page (reader + panel). Can move to a framework later if it grows. |
| **JSON + SQLite** on disk for the library and caches | Simple, inspectable, no database server. |
| **Claude API** via the official `anthropic` SDK | See Section 6. Structured JSON outputs for the glossary pass, prompt caching for repeated questions about the same paper, document citations to quote exact passages from cited papers, and a server-side web search tool for the last-resort lookup. |

---

## 6. LLM usage, cost and privacy

**Where the LLM is used** (each call cached on disk):

1. Glossary enrichment at ingest: one pass per paper, JSON output.
2. On-demand: "what does X mean here?" with the surrounding paragraph.
3. Finding a definition inside a fetched cited paper when the index misses.
4. Plain-English paraphrases and prerequisite lists, on request.
5. Phase 3: web search when no source exists (server-side tool, domain
   allow-list: arxiv.org, nLab, Wikipedia, MathOverflow, …).

**Models.** Default `claude-opus-5` for everything (best at mathematics; the
glossary quality is what the whole tool rests on). A settings toggle can switch
bulk indexing of *cited* papers to `claude-sonnet-5` if you want to cut cost;
that is your call, not a default.

**Rough cost** at list prices (`claude-opus-5`: $5 per million input tokens,
$25 per million output tokens; a 40-page paper is roughly 50k tokens):

| Action | Approximate cost |
|---|---|
| Glossary pass on one paper | $0.40 – $0.60 |
| One on-demand explanation (paper prefix cached) | $0.02 – $0.05 |
| Indexing one cited paper | same as a glossary pass; about 40% of it on `claude-sonnet-5` |
| Web-search lookup | a few cents plus the search fee |

So: under a dollar to open a paper, cents per question afterwards, and nothing
at all for cached answers or when the LLM is switched off.

**No-LLM mode.** Everything deterministic (pandoc, patterns, macro table,
dictionary, definition environments, cited-paper term indexes) works without an
API key. You lose scoped symbol meanings, undefined-term detection, paraphrases
and the last-resort lookups.

**Privacy.** Paper text is sent to the API only when the LLM is on. For
unpublished drafts you can keep it off, or run with the LLM restricted to
cited (already public) papers.

---

## 7. Phased roadmap

### Phase 1 — Prototype (built)

Done means all of the following work on the bundled sample paper and on at
least one real arXiv paper:

- [x] `papassist.bat` / `papassist.sh`: create venv, install dependencies,
      start the server, open the browser. Clear message if Python is missing.
- [x] Drop a `.tex` file, folder or arXiv source archive → paper renders with
      sections, numbered theorem-like blocks, cross-reference links and
      citation markers. Macro table, bibliography and metadata extracted.
- [x] Symbol hover: identifier units tagged; hovering shows the card with
      meaning, location link, trust label; all occurrences highlighted.
      Operators resolve through the standard dictionary. Whole-formula
      fallback card.
- [x] Term hover: definition environments and inline patterns indexed; known
      terms wrapped in the text; hovering shows the definition card.
      Undefined terms show "not defined here" plus citation hints.
- [x] Side panel: preview on hover, pin on click, card stack with breadcrumbs,
      cards themselves hoverable/clickable, pin list exportable as Markdown.
- [x] LLM enrichment (written against the SDK reference and tested with a stand-in client; not yet exercised against the live API from the development sandbox) (if a key is present): glossary pass with scoped symbol
      meanings and undefined-term list; on-demand "what does X mean here?";
      plain-English toggle. All cached. Everything above still works with no
      key.
- [x] Library folder with per-paper `doc.json`, `glossary.json`, cache.
- [x] Tests: a synthetic sample paper covering the tricky cases (nested
      macros, scoped indices, definition variants, multi-file input), plus
      unit tests for the tokeniser and pattern extractors.

Deliberately **not** in Phase 1: fetching cited papers, arXiv HTML input, web
search, prerequisite graph.

### Phase 2 — Cited papers and arXiv (partly built)

- [x] Citation resolution: `.bib`/`.bbl` → arXiv id (bibliography fields, then a
      title search on the arXiv API).
- [x] Fetch and ingest cited sources into the library; jump to "Def. 2.1"-style
      pointers; search cited term and symbol indexes; "Cited paper" cards whose
      text is hoverable and resolves inside that paper.
- [x] arXiv id / URL as input.
- [x] Compact citation labels following the bibliography style, with a
      References section and per-entry index buttons.
- [ ] arXiv HTML (LaTeXML) as an alternative ingest path.
- [ ] The agentic resolver (tool loop) replacing the fixed chain for hard cases.
- [ ] Two-hop recursion with caching (today each hop is a click).

### Phase 3 — Depth and polish

- Web-search fallback with domain allow-list and clear labelling.
- Prerequisite graph for the paper; "what do I need to read Section 3?".
- Library-wide search ("where have I seen 'interleaving' defined before?").
- Cheat-sheet export (Markdown / PDF) per paper.
- Degraded-mode ingest for papers pandoc cannot handle.

### Later / maybe

- PDF input via an external converter.
- Notes and annotations saved with the paper.
- Packaging as a single executable.

---

## 8. Risks and how we handle them

| Risk | Mitigation |
|---|---|
| Real-world LaTeX breaks pandoc (exotic macros, `\def` tricks, custom `.sty`) | Pre-processing pass; degraded text+math view via `pylatexenc`; arXiv-HTML path as an alternative; report unparsed regions instead of failing silently. |
| Tagging units inside formulas changes their appearance | Only identifiers and named operators are wrapped, never bare binary operators; verified today that `\class{}` keeps spacing; whole-formula fallback card. |
| LLM invents a plausible but wrong definition | Trust labels; quotes with locations for anything sourced; LLM answers always shown *with* the context they were derived from; deterministic sources always outrank the LLM. |
| Cited work has no arXiv source | Labelled LLM fallback; manual drop-in of the source into the library. |
| Cost creep from recursive lookups | One-level prefetch only; disk cache; per-paper spend shown in the UI; optional cheaper model for bulk indexing. |
| Windows friction | `pypandoc_binary` wheel avoids a pandoc install; `.bat` checks for Python and prints the install command; paths handled with `pathlib`; test on Windows early. |
| This sandbox cannot reach arXiv (see Section 9) | Bundled sample paper for development; you supply `.tex` files or allow the hosts. |

---

## 9. What I verified today

- **pandoc 3.9** (from the `pypandoc_binary` wheel) on a sample with
  `\newtheorem`, `\input`, `\label`/`\ref`, `\cite[Thm.~4.4]{...}`,
  `\newcommand` and `\DeclareMathOperator`: theorem-like environments become
  labelled, numbered, typed blocks; citations keep keys and suffixes; `\ref`
  becomes a link; macros expand; `\input` is followed. See `tests/fixtures/`
  once the prototype exists.
- **MathJax 3.2.2** keeps `\class{...}{...}` tags on the rendered elements for
  identifiers, grouped expressions and operators, so server-side tagging of
  hover targets works.
- **PyPI** and the **npm registry** are reachable from this development
  environment, so dependencies and a vendored MathJax are fine.
- **Not reachable from this environment** (denied by its network policy):
  `arxiv.org`, `export.arxiv.org`, `ar5iv.labs.arxiv.org`,
  `api.semanticscholar.org`, `cdn.jsdelivr.net`. Development does not need
  them until Phase 2, but testing on real papers does. Either allow those hosts
  in the environment's network settings, or add a few `.tex` sources to the
  repository for me to test on.

---

## 10. Decisions I need from you

Each has my recommended default; "go ahead" means all defaults.

1. **LLM provider and key.** Default: Claude via the Anthropic API, model
   `claude-opus-5`; the app reads `ANTHROPIC_API_KEY` from a local `.env`
   file. Do you have a key, or do you want the prototype to run in no-LLM mode
   first?
2. **Platform.** Default: Windows is the primary target (`.bat`), with a `.sh`
   for Mac/Linux. Is Python 3.11 or newer installed on your machine, or should
   the `.bat` print the install command?
3. **Test papers.** Default: I write one synthetic sample paper in the style of
   topological data analysis. Please also name one to three real arXiv papers
   you actually want to read (ids are enough), and either allow the arXiv hosts
   above or drop their `.tex` sources into the repo.
4. **Hover behaviour.** Default: hover shows a preview after ~250 ms; click
   pins; moving into the panel freezes the preview so you can click inside it.
5. **Trust policy.** Decided: the LLM may **not** answer from general
   knowledge. It only reports what the paper's text establishes, and says
   "the paper does not determine this" otherwise.
6. **Privacy.** Default: paper text is sent to the API when the LLM is on.
   Fine for arXiv papers; say so if you will read unpublished drafts and want
   a stricter default.
7. **Stack.** Default: Python + browser UI as in Section 5. Say so if you would
   rather have something else (VS Code extension, Electron app).

---

## Appendix A — Data model sketch

```json
{
  "paper_id": "arxiv:1207.3674",
  "title": "The structure and stability of persistence modules",
  "blocks": [
    {
      "id": "b12", "kind": "definition", "env": "definition", "number": "2.1",
      "label": "def:pm", "title": "Persistence module", "section": "2",
      "text": "A persistence module is a functor M: (R, <=) -> Vec. We write M_t for M(t) ...",
      "html": "...rendered, with tagged formulas and terms...",
      "math": [
        {"id": "m41", "tex": "M\\colon (\\mathbb{R},\\le)\\to\\mathbf{Vec}",
         "units": [{"id": "u1", "tex": "M"}, {"id": "u2", "tex": "\\mathbb{R}"}, {"id": "u3", "tex": "\\mathbf{Vec}"}]}
      ],
      "cites": [{"key": "chazal2016structure", "suffix": ""}],
      "refs": ["def:pm"]
    }
  ],
  "macros": {"\\Dgm": "\\operatorname{Dgm}", "\\FF": "\\mathbb{F}"},
  "theorem_kinds": {"thm": "theorem", "definition": "definition", "defn": "definition"},
  "bibliography": {
    "chazal2016structure": {"title": "...", "authors": ["Chazal", "de Silva", "Glisse", "Oudot"],
                            "year": 2016, "arxiv": "1207.3674", "doi": null}
  },
  "symbols": [
    {"tex": "M_t", "meaning": "the vector space M(t) that the persistence module M assigns to t",
     "category": "vector space", "scope": {"kind": "paper"}, "defined_at": "b12",
     "source": "paper", "quote": "We write M_t for M(t)"},
    {"tex": "i", "meaning": "an index in {1, ..., n}", "scope": {"kind": "block", "ids": ["b40", "b41"]},
     "defined_at": "b41", "source": "llm_context"}
  ],
  "terms": [
    {"term": "persistence module", "variants": ["persistence modules"], "defined_at": "b12",
     "source": "paper", "depends_on": ["functor", "category of vector spaces"]},
    {"term": "interleaving distance", "defined_at": null, "source": null,
     "cite_hints": ["chazal2016structure"], "mentioned_at": ["b30", "b52"]}
  ]
}
```

## Appendix B — Project layout (planned)

```
Paper_Read_Assist/
  papassist.bat            # Windows launcher: venv, deps, server, browser
  papassist.sh             # Mac/Linux launcher
  requirements.txt
  papassist/               # Python package
    app.py                 # FastAPI app and routes
    ingest/                # pandoc runner, AST -> document model, preamble/macros, bib/bbl, arXiv fetch
    render/                # HTML renderer, math unit tokeniser and \class tagging, term wrapping
    glossary/              # pattern extractors, standard dictionary (JSON), LLM enrichment, merge
    resolve/               # the resolution chain and answer cache
    llm/                   # Anthropic SDK wrapper, prompts, JSON schemas, caching
    library/               # on-disk library management
  web/                     # static front end: index.html, reader.js, panel.js, styles.css, mathjax/ (vendored)
  tests/
    fixtures/sample_paper/ # synthetic multi-file paper exercising the tricky cases
    test_*.py
  docs/PLAN.md             # this document
  library/                 # created at run time, git-ignored
```
