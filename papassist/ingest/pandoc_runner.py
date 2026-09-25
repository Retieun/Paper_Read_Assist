"""Locate the main LaTeX file of a paper and run pandoc on it."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class PandocError(RuntimeError):
    pass


def pandoc_path() -> str:
    """Find pandoc: the pypandoc_binary wheel first, then PATH."""
    try:
        import pypandoc  # type: ignore

        p = pypandoc.get_pandoc_path()
        if p and os.path.exists(p):
            return p
    except Exception:  # pragma: no cover - depends on install
        pass
    p = shutil.which("pandoc")
    if p:
        return p
    raise PandocError(
        "pandoc was not found. Install it with `pip install pypandoc_binary` "
        "or from https://pandoc.org/installing.html"
    )


def find_main_tex(folder: Path) -> Optional[Path]:
    """Pick the .tex file that is the root of the document."""
    cands = [p for p in folder.rglob("*.tex") if not any(part.startswith(".") for part in p.relative_to(folder).parts)]
    if not cands:
        return None
    with_class = []
    for p in cands:
        try:
            head = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"^\s*\\documentclass", head, re.M):
            with_class.append(p)
    if not with_class:
        return cands[0] if len(cands) == 1 else None
    if len(with_class) == 1:
        return with_class[0]
    # Prefer files that contain \begin{document}, then conventional names, then shallow paths.
    def score(p: Path):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            t = ""
        has_doc = "\\begin{document}" in t
        name = p.stem.lower()
        conventional = name in ("main", "paper", "article", "manuscript", "ms", "arxiv")
        return (has_doc, conventional, -len(p.parts), len(t))

    with_class.sort(key=score, reverse=True)
    return with_class[0]


TIKZ_ENVS = ("tikzcd", "tikzpicture", "pspicture", "asy")


def sanitize_tex(tex: str) -> tuple[str, list[str]]:
    """Replace drawing environments with numbered placeholders.

    pandoc cannot render TikZ; left alone, the drawing commands leak into the
    text as garbage. We swap each one for ``[diagram N]`` and keep the source
    so the reader can show it on request.
    """
    diagrams: list[str] = []

    def repl(m: re.Match) -> str:
        diagrams.append(m.group(0))
        return f"\\mbox{{[PAPASSIST-DIAGRAM-{len(diagrams)}]}}"

    for env in TIKZ_ENVS:
        pat = re.compile(r"\\begin\{" + env + r"\}.*?\\end\{" + env + r"\}", re.S)
        tex = pat.sub(repl, tex)
    return tex, diagrams


def run_pandoc(main_tex: Path, workdir: Optional[Path] = None) -> dict:
    """Convert ``main_tex`` to pandoc's JSON AST (as a Python dict)."""
    workdir = workdir or main_tex.parent
    exe = pandoc_path()
    args = [
        exe,
        "-f",
        "latex+latex_macros",
        "-t",
        "json",
        "--resource-path",
        str(workdir),
        str(main_tex),
    ]
    try:
        proc = subprocess.run(args, cwd=str(workdir), capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as e:  # pragma: no cover
        raise PandocError("pandoc timed out after 10 minutes") from e
    if proc.returncode != 0:
        raise PandocError(f"pandoc failed (exit {proc.returncode}):\n{proc.stderr[-4000:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PandocError(f"pandoc produced invalid JSON: {e}") from e
