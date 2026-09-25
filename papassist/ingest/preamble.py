"""Parse the parts of a LaTeX source that pandoc does not report back to us.

We read the main file (and any file it ``\\input``s) for:

* macro definitions (``\\newcommand``, ``\\def``, ``\\DeclareMathOperator``, ``\\let``);
* theorem-like environment declarations (``\\newtheorem``) with their counters,
  reset rules and theorem styles;
* equation numbering rules (``\\numberwithin``);
* bibliography files (``\\bibliography``, ``\\addbibresource``);
* the document class.

The parser is deliberately tolerant: papers contain all sorts of TeX, and a
macro we fail to parse must never stop the paper from loading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class TheoremDecl:
    env: str
    title: str
    counter: str
    reset_by: Optional[str]
    numbered: bool
    style: str  # plain | definition | remark

    @property
    def kind(self) -> str:
        return normalize_theorem_kind(self.title)


@dataclass
class Macro:
    name: str  # without the backslash
    nargs: int
    body: str
    default: Optional[str] = None
    kind: str = "newcommand"  # newcommand | operator | def | let


@dataclass
class Preamble:
    documentclass: Optional[str] = None
    class_options: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    macros: dict[str, Macro] = field(default_factory=dict)
    theorems: dict[str, TheoremDecl] = field(default_factory=dict)
    equation_reset: Optional[str] = None
    bib_files: list[str] = field(default_factory=list)
    bib_style: Optional[str] = None
    biblatex_style: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["theorems"] = {k: {**asdict(v), "kind": v.kind} for k, v in self.theorems.items()}
        return d


KNOWN_KINDS = {
    "definition": "definition",
    "definitions": "definition",
    "notation": "definition",
    "notations": "definition",
    "convention": "definition",
    "conventions": "definition",
    "terminology": "definition",
    "theorem": "theorem",
    "lemma": "lemma",
    "proposition": "proposition",
    "corollary": "corollary",
    "claim": "claim",
    "fact": "fact",
    "conjecture": "conjecture",
    "question": "question",
    "problem": "problem",
    "remark": "remark",
    "remarks": "remark",
    "example": "example",
    "examples": "example",
    "construction": "construction",
    "assumption": "assumption",
    "hypothesis": "assumption",
    "observation": "remark",
    "warning": "remark",
    "exercise": "example",
    "algorithm": "construction",
    "setup": "definition",
    "standing assumption": "assumption",
}


def normalize_theorem_kind(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"[^a-z ]", "", t)
    if t in KNOWN_KINDS:
        return KNOWN_KINDS[t]
    for key, kind in KNOWN_KINDS.items():
        if t.startswith(key):
            return kind
    return t.split(" ")[0] if t else "theorem"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def strip_comments(tex: str) -> str:
    """Remove ``%`` comments (but not ``\\%``)."""
    out = []
    for line in tex.split("\n"):
        i = 0
        cut = None
        while i < len(line):
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "%":
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def read_group(s: str, i: int) -> tuple[str, int]:
    """``s[i]`` must be ``{``. Return (content, index after the closing brace)."""
    assert s[i] == "{", (i, s[i : i + 10])
    depth = 0
    j = i
    while j < len(s):
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
        j += 1
    raise ValueError("unbalanced braces")


def skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\n\r":
        i += 1
    return i


def read_optional(s: str, i: int) -> tuple[Optional[str], int]:
    """Read ``[...]`` at position i (after whitespace) if present."""
    j = skip_ws(s, i)
    if j < len(s) and s[j] == "[":
        depth = 0
        k = j
        while k < len(s):
            ch = s[k]
            if ch == "\\":
                k += 2
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "]" and depth == 0:
                return s[j + 1 : k], k + 1
            k += 1
    return None, i


def read_cs_name(s: str, i: int) -> tuple[Optional[str], int]:
    """Read a control sequence name (``\\foo``) at i, or ``{\\foo}``."""
    j = skip_ws(s, i)
    if j < len(s) and s[j] == "{":
        inner, k = read_group(s, j)
        inner = inner.strip()
        if inner.startswith("\\"):
            return inner[1:], k
        return None, i
    if j < len(s) and s[j] == "\\":
        m = re.match(r"\\([A-Za-z@]+|.)", s[j:])
        if m:
            return m.group(1), j + m.end()
    return None, i


def _read_macro_arg(s: str, i: int) -> tuple[Optional[str], int]:
    j = skip_ws(s, i)
    if j < len(s) and s[j] == "{":
        return read_group(s, j)
    # single-token bodies like \newcommand\foo\bar
    m = re.match(r"\\[A-Za-z@]+|\\.|.", s[j:], re.S)
    if m:
        return m.group(0), j + m.end()
    return None, i


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

INPUT_RE = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")


def gather_sources(main_path: Path, max_files: int = 60) -> list[tuple[Path, str]]:
    """Return [(path, text)] for the main file and everything it inputs."""
    seen: set[Path] = set()
    result: list[tuple[Path, str]] = []
    base = main_path.parent

    def visit(p: Path):
        if p in seen or len(result) >= max_files:
            return
        seen.add(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        result.append((p, text))
        for m in INPUT_RE.finditer(strip_comments(text)):
            name = m.group(1).strip()
            cand = base / name
            if cand.suffix == "":
                cand = cand.with_suffix(".tex")
            if cand.exists():
                visit(cand)

    visit(main_path)
    return result


def parse_preamble(tex: str) -> Preamble:
    """Parse one file's worth of TeX. Use :func:`parse_sources` for whole projects."""
    pre = Preamble()
    _parse_into(pre, tex)
    return pre


def parse_sources(main_path: Path) -> Preamble:
    pre = Preamble()
    for _, text in gather_sources(main_path):
        _parse_into(pre, text)
    return pre


def _parse_into(pre: Preamble, tex: str) -> None:
    s = strip_comments(tex)

    m = re.search(r"\\documentclass\s*(\[[^\]]*\])?\s*\{([^}]+)\}", s)
    if m and pre.documentclass is None:
        pre.documentclass = m.group(2).strip()
        if m.group(1):
            pre.class_options = [o.strip() for o in m.group(1)[1:-1].split(",") if o.strip()]

    for m in re.finditer(r"\\usepackage\s*(\[[^\]]*\])?\s*\{([^}]+)\}", s):
        names = [p.strip() for p in m.group(2).split(",") if p.strip()]
        pre.packages.extend(names)
        if "biblatex" in names and m.group(1):
            sm = re.search(r"(?:bibstyle|citestyle|style)\s*=\s*([\w\-]+)", m.group(1))
            if sm:
                pre.biblatex_style = sm.group(1)

    for m in re.finditer(r"\\(?:bibliography)\s*\{([^}]+)\}", s):
        pre.bib_files.extend(b.strip() for b in m.group(1).split(",") if b.strip())
    for m in re.finditer(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}", s):
        pre.bib_files.append(m.group(1).strip())
    m = re.search(r"\\bibliographystyle\s*\{([^}]+)\}", s)
    if m:
        pre.bib_style = m.group(1).strip()

    m = re.search(r"\\numberwithin\s*\{equation\}\s*\{(\w+)\}", s)
    if m:
        pre.equation_reset = m.group(1)

    _parse_macros(pre, s)
    _parse_theorems(pre, s)


MACRO_RE = re.compile(r"\\(newcommand|renewcommand|providecommand|DeclareMathOperator|def|let|DeclareRobustCommand)(\*?)")


def _parse_macros(pre: Preamble, s: str) -> None:
    for m in MACRO_RE.finditer(s):
        cmd, star = m.group(1), m.group(2)
        i = m.end()
        try:
            if cmd in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"):
                name, i = read_cs_name(s, i)
                if not name:
                    continue
                nargs_s, i = read_optional(s, i)
                nargs = int(nargs_s) if nargs_s and nargs_s.strip().isdigit() else 0
                default, i = read_optional(s, i) if nargs_s is not None else (None, i)
                body, i = _read_macro_arg(s, i)
                if body is None:
                    continue
                if cmd == "providecommand" and name in pre.macros:
                    continue
                pre.macros[name] = Macro(name, nargs, body.strip(), default, "newcommand")
            elif cmd == "DeclareMathOperator":
                name, i = read_cs_name(s, i)
                if not name:
                    continue
                body, i = _read_macro_arg(s, i)
                if body is None:
                    continue
                op = "\\operatorname*" if star else "\\operatorname"
                pre.macros[name] = Macro(name, 0, f"{op}{{{body.strip()}}}", None, "operator")
            elif cmd == "def":
                name, i = read_cs_name(s, i)
                if not name:
                    continue
                # parameter text like #1#2 up to the opening brace
                j = skip_ws(s, i)
                k = j
                while k < len(s) and s[k] != "{":
                    k += 1
                params = s[j:k]
                if re.fullmatch(r"(#\d)*", params.strip()):
                    nargs = len(re.findall(r"#\d", params))
                    if k < len(s) and s[k] == "{":
                        body, i = read_group(s, k)
                        pre.macros[name] = Macro(name, nargs, body.strip(), None, "def")
            elif cmd == "let":
                name, i = read_cs_name(s, i)
                if not name:
                    continue
                j = skip_ws(s, i)
                if j < len(s) and s[j] == "=":
                    j += 1
                target, i = read_cs_name(s, j)
                if target:
                    pre.macros[name] = Macro(name, 0, "\\" + target, None, "let")
        except (ValueError, IndexError):
            continue


THEOREM_RE = re.compile(r"\\(newtheorem|theoremstyle|declaretheorem)(\*?)")


def _parse_theorems(pre: Preamble, s: str) -> None:
    style = "plain"
    for m in THEOREM_RE.finditer(s):
        cmd, star = m.group(1), m.group(2)
        i = m.end()
        try:
            if cmd == "theoremstyle":
                j = skip_ws(s, i)
                if j < len(s) and s[j] == "{":
                    st, _ = read_group(s, j)
                    style = st.strip() or "plain"
            elif cmd == "newtheorem":
                j = skip_ws(s, i)
                if j >= len(s) or s[j] != "{":
                    continue
                env, i = read_group(s, j)
                env = env.strip()
                shared, i = read_optional(s, i)
                j = skip_ws(s, i)
                if j >= len(s) or s[j] != "{":
                    continue
                title, i = read_group(s, j)
                reset, i = read_optional(s, i) if shared is None else (None, i)
                counter = shared.strip() if shared else env
                pre.theorems[env] = TheoremDecl(
                    env=env,
                    title=title.strip(),
                    counter=counter,
                    reset_by=reset.strip() if reset else (pre.theorems[counter].reset_by if counter in pre.theorems else None),
                    numbered=not star,
                    style=style,
                )
            elif cmd == "declaretheorem":  # thmtools
                opts, i = read_optional(s, i)
                j = skip_ws(s, i)
                if j >= len(s) or s[j] != "{":
                    continue
                env, i = read_group(s, j)
                env = env.strip()
                title = env.capitalize()
                counter, reset, numbered, st = env, None, True, style
                if opts:
                    for part in re.split(r",(?![^{]*})", opts):
                        if "=" not in part:
                            continue
                        k, v = [x.strip() for x in part.split("=", 1)]
                        v = v.strip("{}")
                        if k == "name":
                            title = v
                        elif k in ("sibling", "sharecounter"):
                            counter = v
                        elif k in ("numberwithin", "parent", "within"):
                            reset = v
                        elif k == "numbered" and v == "no":
                            numbered = False
                        elif k == "style":
                            st = v
                pre.theorems[env] = TheoremDecl(env, title, counter, reset, numbered, st)
        except (ValueError, IndexError):
            continue
    # proof is always available with amsthm
    pre.theorems.setdefault("proof", TheoremDecl("proof", "Proof", "proof", None, False, "proof"))


def expand_macros_for_mathjax(pre: Preamble) -> dict:
    """Return a MathJax ``macros`` config dict for the paper's own macros.

    pandoc already expands most macros in math, but this is a safety net for
    anything it leaves alone (``\\def`` bodies, macros used inside ``\\text``).
    """
    out: dict = {}
    for name, mac in pre.macros.items():
        if not re.fullmatch(r"[A-Za-z]+", name):
            continue
        if name in _MATHJAX_RESERVED:
            continue
        body = mac.body
        if mac.kind == "let":
            body = mac.body
        if mac.nargs:
            if mac.default is not None:
                out[name] = [body, mac.nargs, mac.default]
            else:
                out[name] = [body, mac.nargs]
        else:
            out[name] = body
    return out


_MATHJAX_RESERVED = {
    "begin", "end", "left", "right", "frac", "sqrt", "text", "mathrm", "mathbf", "mathbb",
    "mathcal", "mathfrak", "operatorname", "sum", "int", "prod", "lim", "label", "tag",
    "class", "cssId", "href", "style", "displaystyle", "textstyle", "over", "atop",
}
