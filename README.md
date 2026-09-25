# PapAssist

A reading assistant for mathematics papers. Give it a paper as LaTeX source, read it in
your browser, and **hover over any symbol in a formula or any technical term** to see what
it means *in this paper*, where it was introduced, and where the explanation came from.

Every answer carries its source:

| Label | Meaning |
|---|---|
| **This paper** | quoted from the paper itself, with a jump link to the place (explicit statements such as "Let $k$ be a field", "we write $\Delta_W$ for …", ":=", definition environments, inline definitions) |
| **Standard notation** | from a built-in dictionary of common notation; used only when the paper does not say otherwise |
| **Inferred from this paper's text (LLM)** | optional: Claude reads the paper's own text and reports what it establishes, with the supporting quote. It never answers from outside knowledge; if the paper does not determine a meaning, it says so |

Nothing is invented: if a symbol or term is not defined in the paper, the card says so and
shows where it first appears.

## Quick start

Requirements: Python 3.11 or newer. Everything else (including pandoc) is installed into a
local virtual environment on first run.

**Windows**: double-click `papassist.bat` (or run `papassist.bat C:\path\to\paper.tex`).
**macOS / Linux**: `./papassist.sh` (or `./papassist.sh path/to/paper.tex`).

A browser tab opens at <http://127.0.0.1:8765/>. Drop in:

* a `.tex` file together with its `.bib` (select both, or drop a folder as a `.zip`),
* an arXiv source archive (`.tar.gz`),
* or type a path on your computer into the box.

The paper is converted (a few seconds), typeset with MathJax, and the glossary is built.

### Reading

* **Hover** a symbol, an emphasised term, a citation or a "Theorem 2.5" link: the panel on
  the right shows a preview card. All other occurrences of the symbol are highlighted.
* **Click** to keep the card. Cards are themselves hoverable and clickable, so a definition
  that uses another notion can be followed down; breadcrumbs take you back.
* **Pin** cards you want to keep; the Pinned tab exports them as a Markdown cheat sheet.
* The **Glossary** tab lists every symbol and term the paper explains.

### Optional: the LLM

Copy `.env.example` to `.env` and put your Anthropic API key in it:

```
ANTHROPIC_API_KEY=sk-ant-...
```

With a key present, opening a paper also runs one glossary pass over the whole text
(model `claude-opus-5` by default, roughly half a dollar for a 40-page paper; the result
is cached next to the paper), and cards gain two buttons: *Explain in plain English* for a
sourced definition, and *Ask the LLM what this paper says about it* for anything the
deterministic pass could not resolve. Both answers are labelled and cached. Run with
`--no-llm` to disable all API calls, or set `PAPASSIST_MODEL` to use another Claude model.

## Development

```
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m papassist tests/fixtures/sample_paper --no-browser
```

Layout: `papassist/ingest` (LaTeX → document model), `papassist/render` (formula tagging,
HTML), `papassist/glossary` (extractors, dictionary), `papassist/resolve` (cards),
`papassist/llm` (Claude), `papassist/library` (on-disk store), `web/` (front end),
`tests/fixtures/sample_paper` (a small synthetic paper used by the tests).
