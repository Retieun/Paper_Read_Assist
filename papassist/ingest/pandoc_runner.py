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


_DOCCLASS_RE = re.compile(r"^[ \t]*\\documentclass\b", re.M)
_SUBDOC_RE = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{(?:subfiles|standalone)\}")


def _tex_head(p: Path, n: int = 20000) -> str:
    try:
        data = p.read_bytes()[:n]
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace").lstrip("\ufeff")
    # drop comment lines so a commented-out \documentclass does not count
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("%"))


def list_tex_files(folder: Path) -> list[Path]:
    out = []
    for p in folder.rglob("*.tex"):
        rel = p.relative_to(folder).parts
        if any(part.startswith(".") or part == "__MACOSX" for part in rel) or p.name.startswith("._"):
            continue
        out.append(p)
    return out


def find_main_tex(folder: Path) -> Optional[Path]:
    """Pick the .tex file that is the root of the document."""
    cands = list_tex_files(folder)
    if not cands:
        return None
    with_class = [p for p in cands if _DOCCLASS_RE.search(_tex_head(p))]
    if not with_class:
        return cands[0] if len(cands) == 1 else None
    if len(with_class) == 1:
        return with_class[0]

    def score(p: Path):
        t = _tex_head(p, 200000)
        full = p.read_text(encoding="utf-8", errors="replace") if p.stat().st_size < 5_000_000 else t
        has_doc = "\\begin{document}" in full
        is_sub = bool(_SUBDOC_RE.search(t))
        name = p.stem.lower()
        conventional = name in ("main", "paper", "article", "manuscript", "ms", "arxiv", "draft", "thesis", "book")
        return (not is_sub, has_doc, conventional, -len(p.parts), len(full))

    with_class.sort(key=score, reverse=True)
    return with_class[0]


def wrap_fragment(folder: Path) -> Optional[Path]:
    """No file has \\documentclass: wrap the most document-like .tex in a minimal article so it still renders."""
    cands = list_tex_files(folder)
    if not cands:
        return None

    def score(p: Path):
        t = _tex_head(p, 200000)
        return (t.count("\\section") + t.count("\\begin{"), p.stat().st_size)

    body = max(cands, key=score)
    main = folder / "papassist_main.tex"
    rel = body.relative_to(folder).as_posix()
    main.write_text(
        "\\documentclass{article}\n\\usepackage{amsmath,amssymb,amsthm}\n"
        "\\newtheorem{theorem}{Theorem}[section]\\newtheorem{lemma}[theorem]{Lemma}\\newtheorem{proposition}[theorem]{Proposition}"
        "\\newtheorem{corollary}[theorem]{Corollary}\\newtheorem{definition}[theorem]{Definition}\\newtheorem{remark}[theorem]{Remark}\\newtheorem{example}[theorem]{Example}\n"
        f"\\begin{{document}}\n\\input{{{rel}}}\n\\end{{document}}\n",
        encoding="utf-8",
    )
    return main


def describe_folder(folder: Path, limit: int = 12) -> str:
    """A short listing used in error messages."""
    names = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            names.append(p.relative_to(folder).as_posix())
        if len(names) >= limit:
            names.append("…")
            break
    return ", ".join(names) if names else "(empty)"


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


def run_pandoc(main_tex: Path, workdir: Optional[Path] = None, timeout: int = 600) -> dict:
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
        # pandoc always speaks UTF-8; never let Windows' locale code page (cp1252) decode it
        proc = subprocess.run(
            args, cwd=str(workdir), capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
        )
    except subprocess.TimeoutExpired as e:  # pragma: no cover
        raise PandocError(f"pandoc timed out after {timeout // 60} minutes") from e
    if proc.returncode != 0:
        raise PandocError(f"pandoc failed (exit {proc.returncode}):\n{proc.stderr[-4000:]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise PandocError(f"pandoc produced invalid JSON: {e}") from e


_STYLE_SUFFIXES = (".sty", ".cls", ".clo", ".bst")


def local_style_files(folder: Path) -> list[Path]:
    """Style/class files shipped with the sources (journal templates, macro packages)."""
    out = []
    for p in folder.rglob("*"):
        if p.suffix.lower() not in _STYLE_SUFFIXES or not p.is_file():
            continue
        rel = p.relative_to(folder).parts
        if any(part.startswith(".") or part == "__MACOSX" for part in rel):
            continue
        out.append(p)
    return sorted(out)


def run_pandoc_with_fallback(main_tex: Path, workdir: Optional[Path] = None) -> tuple[dict, list[str]]:
    """Run pandoc; if it fails, retry once with the local .sty/.cls files hidden.

    pandoc reads local style files to learn macros, which is what we want for a
    ``macros.sty`` -- but a journal template full of TeX internals can make it
    give up.  The second attempt renames those files out of the way (and puts
    them back afterwards), so the paper still opens, at the cost of macros that
    were only defined in those files.  Returns the AST and a list of warnings.
    """
    workdir = workdir or main_tex.parent
    styles = local_style_files(workdir)
    try:
        # a template that sends pandoc into a loop should not cost the reader ten minutes
        return run_pandoc(main_tex, workdir=workdir, timeout=180 if styles else 600), []
    except PandocError as first:
        if not styles:
            raise
        hidden: list[tuple[Path, Path]] = []
        try:
            for sp in styles:
                off = sp.with_name(sp.name + ".papassist-off")
                sp.rename(off)
                hidden.append((sp, off))
            try:
                ast = run_pandoc(main_tex, workdir=workdir)
            except PandocError:
                raise first
        finally:
            for sp, off in hidden:
                if off.exists() and not sp.exists():
                    off.rename(sp)
        names = ", ".join(sp.relative_to(workdir).as_posix() for sp in styles)
        return ast, [
            f"pandoc could not read the paper with its local style files ({names}); "
            f"they were ignored, so macros defined only there may render as their names. "
            f"Original error: {str(first).strip().splitlines()[-1][:300] if str(first).strip() else 'unknown'}"
        ]
