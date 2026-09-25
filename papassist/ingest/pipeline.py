"""One call to turn a folder of LaTeX into a Document."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .ast_to_doc import DocBuilder
from .bib import load_bibliography
from .document import Document
from .pandoc_runner import PandocError, find_main_tex, run_pandoc, sanitize_tex
from .preamble import parse_sources


def sanitize_folder(folder: Path) -> list[str]:
    """Rewrite every .tex file in ``folder`` so drawings become placeholders.

    Only ever call this on PapAssist's own copy of the sources.
    """
    diagrams: list[str] = []
    for p in sorted(folder.rglob("*.tex")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(f"\\begin{{{env}}}" in text for env in ("tikzcd", "tikzpicture", "pspicture", "asy")):
            continue
        new, found = sanitize_tex(text)
        if found:
            # renumber placeholders so they are unique across files
            offset = len(diagrams)

            def renum(m: re.Match) -> str:
                return f"[PAPASSIST-DIAGRAM-{int(m.group(1)) + offset}]"

            new = re.sub(r"\[PAPASSIST-DIAGRAM-(\d+)\]", renum, new)
            diagrams.extend(found)
            p.write_text(new, encoding="utf-8")
    return diagrams


def ingest_folder(folder: Path, paper_id: str, main: Optional[Path] = None) -> Document:
    diagrams = sanitize_folder(folder)
    main = main or find_main_tex(folder)
    if main is None:
        raise PandocError("No .tex file with \\documentclass was found.")
    pre = parse_sources(main)
    ast = run_pandoc(main, workdir=main.parent)
    bib = load_bibliography(main, pre.bib_files)
    doc = DocBuilder(ast, pre, bib, paper_id, diagrams=diagrams, source_main=str(main.relative_to(folder))).build()
    return doc
