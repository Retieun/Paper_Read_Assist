# PapAssist

A reading assistant for mathematics papers. Give it a paper as LaTeX source, read it in
your browser, and **hover over any symbol in a formula or any technical term** to see what
it means *in this paper*, where it was introduced, and where the explanation came from.

Every answer carries its source:

| Label | Meaning |
|---|---|
| **This paper** | quoted from the paper itself, with a jump link to the place (explicit statements such as "Let $k$ be a field", "we write $\Delta_W$ for …", ":=", definition environments, inline definitions) |
| **Cited paper** | quoted from a paper this paper cites, after its LaTeX source was fetched from arXiv and indexed the same way; a citation pointer such as "[8, Definition 2.1]" is followed directly |
| **Standard notation** | from a built-in dictionary of common notation; used only when the paper does not say otherwise |
| **Inferred from this paper's text (LLM)** | optional: Claude reads the paper's own text and reports what it establishes, with the supporting quote. It never answers from outside knowledge; if the paper does not determine a meaning, it says so |

Nothing is invented: if a symbol or term is not defined in the paper, the card says so and
shows where it first appears.

## Quick start

Requirements: Python 3.11 or newer. Everything else (including pandoc) is installed into a
local virtual environment on first run.

**Windows**: double-click `papassist.bat` (or run `papassist.bat C:\path\to\paper.tex`).
**macOS / Linux**: `./papassist.sh` (or `./papassist.sh path/to/paper.tex`). macOS ships an old
Python; install 3.11 or newer from [python.org](https://www.python.org/downloads/macos/) or with
`brew install python@3.12`, and the launcher picks the newest one it finds. If the file is not
executable after downloading, `bash papassist.sh` works too.

Both launchers create a private Python environment in `.venv` on the first run and install the
dependencies into it (pip packages installed elsewhere, for example with Homebrew, are not
used). If that first installation fails or is interrupted, simply run the launcher again: it
checks the environment and finishes the installation. Deleting the `.venv` folder starts over.

A browser tab opens at <http://127.0.0.1:8765/>. Drop in:

* a `.tex` file together with its `.bib` (select both, or drop a folder as a `.zip`),
* a project archive as exported by Overleaf or zipped from a folder (`.zip`, with figures,
  subfolders, `\input` files, local `.sty`/`.cls` files, `.bbl`, ...),
* an arXiv source archive (`.tar.gz`),
* or type a path on your computer into the box.

Files can be dropped anywhere on the page, also while another paper is open. The paper is
converted (a few seconds), typeset with MathJax, and the glossary is built. Figures in
PNG/JPG/GIF/SVG format are shown in place; PDF/EPS figures appear as a link that opens the
file in a new tab. If the conversion fails, the message names the cause and lists the files
that were received.

### Running in a GitHub Codespace

The repository carries a dev-container definition, so a Codespace comes with the Python
environment ready:

1. On GitHub: *Code → Codespaces → Create codespace* (pick the branch you want to try).
2. In the Codespace terminal run `./papassist.sh`. VS Code forwards port 8765 and opens the
   app in a browser tab; the address is also listed under the *PORTS* tab.
3. Open papers as usual. Files are uploaded to the Codespace in small pieces and converted
   in the background, so the forwarding proxy cannot cut the request off.

If an upload still fails, drag the archive into the VS Code file explorer (it lands in
`/workspaces/Paper_Read_Assist/`) and type that path into the path box. GitHub Actions
cannot host the app: a workflow runner has no browser that can reach it.

### Reading

* **Hover** a symbol, an emphasised term, a citation or a "Theorem 2.5" link: the panel on
  the right shows a preview card. All other occurrences of the symbol are highlighted.
* **Click** to keep the card. Cards are themselves hoverable and clickable, so a definition
  that uses another notion can be followed down; breadcrumbs take you back.
* **Shift-click** a second symbol in the same formula to explain the whole span between them
  (MathJax output has no selectable text, so this replaces drag-selection).
* Citations render as in the PDF (`[12]`, or `[SW26]` for alpha styles) and jump to a
  References section at the end; the full reference shows on hover.
* **Cited papers.** When a term or symbol is not defined in the paper, the card offers the
  references cited next to it: *fetch* downloads that paper's LaTeX source from arXiv (by the
  identifier in the bibliography, or by a title search) and indexes it in your library, and
  *search* looks the term up there, following citation pointers like "Thm. 4.4". Hits appear
  on the card under **In cited papers** with the cited paper's own text, which is hoverable in
  turn. The References section has the same *index* buttons per entry, and the front page can
  open any arXiv paper by id.
* **Pin** cards you want to keep; the Pinned tab exports them as a Markdown cheat sheet.
* The **Glossary** tab lists every symbol and term the paper explains. Drag the panel's left
  edge to resize it.

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
