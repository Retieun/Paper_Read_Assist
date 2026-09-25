"""Split TeX math into *identifier units* and tag them for hover.

A unit is the thing a reader would point at and ask "what is this?":

* a Latin or Greek letter with its sub/superscripts and primes (``M_t``, ``\\varphi_M``, ``f'``);
* a styled letter (``\\mathbb{R}``, ``\\mathcal{M}``, ``\\widetilde{H}``) with scripts;
* a named operator (``\\operatorname{lk}_\\Delta``, ``\\mathrm{PH}``);
* a big operator with its limits (``\\bigoplus_{j\\in T}``);
* a standalone symbol such as ``\\infty`` or ``\\partial``.

Units nest: ``\\mathbb{D}^q_{i,b}`` contains the units ``q``, ``i`` and ``b``.

Every unit is wrapped as ``\\class{pa-u-N}{...}`` so that MathJax puts the class
on the rendered element; the browser reads it back on hover. Binary operators
and relations (``\\otimes``, ``\\to``) are left alone and resolved by code point.

The tokenizer is tolerant: anything it does not understand becomes an
untagged span, and a failure anywhere yields "no units" rather than an error,
so rendering is never blocked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

@dataclass
class Tok:
    kind: str  # cs | char | lbrace | rbrace | sup | sub | amp
    text: str
    start: int
    end: int


def tokenize(tex: str) -> list[Tok]:
    toks: list[Tok] = []
    i, n = 0, len(tex)
    while i < n:
        ch = tex[i]
        if ch == "\\":
            if i + 1 < n and tex[i + 1].isalpha():
                j = i + 1
                while j < n and tex[j].isalpha():
                    j += 1
                if j < n and tex[j] == "*" and tex[i + 1 : j] in ("operatorname", "sideset", "mathop"):
                    j += 1
                toks.append(Tok("cs", tex[i:j], i, j))
                i = j
            elif i + 1 < n:
                toks.append(Tok("cs", tex[i : i + 2], i, i + 2))
                i += 2
            else:
                toks.append(Tok("char", ch, i, i + 1))
                i += 1
        elif ch == "{":
            toks.append(Tok("lbrace", ch, i, i + 1)); i += 1
        elif ch == "}":
            toks.append(Tok("rbrace", ch, i, i + 1)); i += 1
        elif ch == "^":
            toks.append(Tok("sup", ch, i, i + 1)); i += 1
        elif ch == "_":
            toks.append(Tok("sub", ch, i, i + 1)); i += 1
        elif ch == "&":
            toks.append(Tok("amp", ch, i, i + 1)); i += 1
        elif ch.isspace():
            i += 1
        else:
            toks.append(Tok("char", ch, i, i + 1)); i += 1
    return toks


# ---------------------------------------------------------------------------
# Command tables
# ---------------------------------------------------------------------------

STYLE_CMDS = {
    "\\mathbb", "\\mathcal", "\\mathfrak", "\\mathrm", "\\mathbf", "\\mathsf", "\\mathtt",
    "\\mathit", "\\mathscr", "\\mathnormal", "\\boldsymbol", "\\bm", "\\pmb", "\\mathds",
    "\\mathbold", "\\symbf", "\\symcal", "\\symbb",
}
ACCENT_CMDS = {
    "\\widetilde", "\\tilde", "\\hat", "\\widehat", "\\bar", "\\overline", "\\underline", "\\vec",
    "\\dot", "\\ddot", "\\check", "\\breve", "\\acute", "\\grave", "\\mathring", "\\overrightarrow",
    "\\overleftarrow", "\\dddot", "\\widecheck", "\\overbar", "\\underbar",
}
OPNAME_CMDS = {"\\operatorname", "\\operatorname*", "\\mathop", "\\DeclareMathOperator"}
TEXT_CMDS = {
    "\\text", "\\textrm", "\\textbf", "\\textit", "\\textsf", "\\texttt", "\\textup", "\\textnormal",
    "\\mbox", "\\hbox", "\\label", "\\tag", "\\tag*", "\\ref", "\\eqref", "\\hphantom", "\\vphantom",
    "\\phantom", "\\color", "\\textcolor", "\\mathstrut", "\\intertext", "\\shortintertext",
    "\\rule", "\\hspace", "\\hspace*", "\\vspace", "\\kern", "\\mkern", "\\mskip", "\\raisebox",
    "\\emph", "\\textsc", "\\cite", "\\footnote", "\\stackrel*",
}
# command -> number of mandatory brace arguments whose contents are math
ARG_CMDS = {
    "\\frac": 2, "\\dfrac": 2, "\\tfrac": 2, "\\cfrac": 2, "\\binom": 2, "\\dbinom": 2, "\\tbinom": 2,
    "\\overset": 2, "\\underset": 2, "\\stackrel": 2, "\\sqrt": 1, "\\underbrace": 1, "\\overbrace": 1,
    "\\substack": 1, "\\boxed": 1, "\\xrightarrow": 1, "\\xleftarrow": 1, "\\xmapsto": 1,
    "\\xrightleftharpoons": 1, "\\overset*": 2, "\\genfrac": 6, "\\sideset": 3, "\\sideset*": 3,
    "\\mathclap": 1, "\\mathrlap": 1, "\\mathllap": 1, "\\smash": 1, "\\prescript": 3,
    "\\underbracket": 1, "\\overbracket": 1, "\\not": 0,
}
OPT_ARG_CMDS = {"\\sqrt", "\\xrightarrow", "\\xleftarrow", "\\xmapsto", "\\xrightleftharpoons", "\\smash", "\\sideset"}
DELIM_CMDS = {
    "\\left", "\\right", "\\middle", "\\big", "\\Big", "\\bigg", "\\Bigg", "\\bigl", "\\bigr", "\\Bigl",
    "\\Bigr", "\\biggl", "\\biggr", "\\Biggl", "\\Biggr", "\\bigm", "\\Bigm", "\\biggm", "\\Biggm",
}
GREEK = {
    "\\alpha", "\\beta", "\\gamma", "\\delta", "\\epsilon", "\\varepsilon", "\\zeta", "\\eta", "\\theta",
    "\\vartheta", "\\iota", "\\kappa", "\\varkappa", "\\lambda", "\\mu", "\\nu", "\\xi", "\\pi", "\\varpi",
    "\\rho", "\\varrho", "\\sigma", "\\varsigma", "\\tau", "\\upsilon", "\\phi", "\\varphi", "\\chi",
    "\\psi", "\\omega", "\\Gamma", "\\Delta", "\\Theta", "\\Lambda", "\\Xi", "\\Pi", "\\Sigma", "\\Upsilon",
    "\\Phi", "\\Psi", "\\Omega", "\\varGamma", "\\varDelta", "\\varTheta", "\\varLambda", "\\varXi", "\\varPi",
    "\\varSigma", "\\varUpsilon", "\\varPhi", "\\varPsi", "\\varOmega", "\\digamma",
}
NAMED_SYMS = {
    "\\ell", "\\hbar", "\\imath", "\\jmath", "\\aleph", "\\beth", "\\gimel", "\\daleth", "\\infty",
    "\\partial", "\\nabla", "\\emptyset", "\\varnothing", "\\wp", "\\Re", "\\Im", "\\mho", "\\eth",
    "\\bullet", "\\ast", "\\star", "\\top", "\\bot", "\\square", "\\Box", "\\diamond", "\\Diamond", "\\triangle",
}
BIGOPS = {
    "\\sum", "\\prod", "\\coprod", "\\bigoplus", "\\bigotimes", "\\bigcup", "\\bigcap", "\\bigsqcup",
    "\\bigvee", "\\bigwedge", "\\biguplus", "\\bigodot", "\\int", "\\iint", "\\iiint", "\\oint",
    "\\lim", "\\limsup", "\\liminf", "\\varinjlim", "\\varprojlim", "\\injlim", "\\projlim", "\\colim",
    "\\sup", "\\inf", "\\max", "\\min", "\\argmax", "\\argmin", "\\det", "\\dim", "\\ker", "\\deg",
    "\\gcd", "\\hom", "\\Pr", "\\exp", "\\log", "\\ln", "\\sin", "\\cos", "\\tan", "\\arg",
}
SPACING_CMDS = {
    "\\,", "\;", "\\!", "\\:", "\\ ", "\\quad", "\\qquad", "\\enspace", "\\thinspace", "\\negthinspace",
    "\\displaystyle", "\\textstyle", "\\scriptstyle", "\\scriptscriptstyle", "\\nolimits", "\\limits",
    "\\allowbreak", "\\nonumber", "\\notag", "\\mathstrut", "\\strut",
}


# ---------------------------------------------------------------------------
# Item tree
# ---------------------------------------------------------------------------

@dataclass
class Script:
    op: str  # '^' | '_' | "'"
    op_start: int
    op_end: int
    arg: Optional["Item"]


@dataclass
class Item:
    kind: str  # atom | group | other | text | cmd | num
    start: int
    end: int
    atom_type: str = ""  # id | greek | sym | styled | opname | bigop
    cs: str = ""
    base_end: int = 0
    scripts: list[Script] = field(default_factory=list)
    children: list["Item"] = field(default_factory=list)
    unit_id: Optional[int] = None


class _Parser:
    def __init__(self, tex: str):
        self.tex = tex
        self.toks = tokenize(tex)

    # -- entry points -----------------------------------------------------
    def parse(self) -> list[Item]:
        items, _ = self.parse_seq(0, until_rbrace=False)
        return items

    def parse_seq(self, i: int, until_rbrace: bool) -> tuple[list[Item], int]:
        items: list[Item] = []
        toks = self.toks
        while i < len(toks):
            t = toks[i]
            if t.kind == "rbrace":
                if until_rbrace:
                    return items, i + 1
                items.append(Item("other", t.start, t.end))
                i += 1
                continue
            if t.kind in ("sup", "sub"):
                arg, j = self.parse_arg(i + 1)
                end = arg.end if arg else t.end
                target = items[-1] if items and items[-1].kind == "atom" else None
                if target is not None:
                    target.scripts.append(Script(t.text, t.start, t.end, arg))
                    target.end = end
                else:
                    other = Item("other", t.start, end)
                    if arg is not None:
                        other.children = [arg]
                    items.append(other)
                i = j
                continue
            if t.kind == "char" and t.text == "'":
                target = items[-1] if items and items[-1].kind == "atom" else None
                if target is not None:
                    target.scripts.append(Script("'", t.start, t.end, None))
                    target.end = t.end
                else:
                    items.append(Item("other", t.start, t.end))
                i += 1
                continue
            item, i = self.parse_one(i, in_arg=False)
            if item is not None:
                items.append(item)
        return items, i

    def parse_arg(self, i: int) -> tuple[Optional[Item], int]:
        """Parse exactly one argument (a group or a single token/command)."""
        toks = self.toks
        if i >= len(toks):
            return None, i
        t = toks[i]
        if t.kind in ("sup", "sub", "rbrace", "amp"):
            return None, i
        return self.parse_one(i, in_arg=True)

    def parse_one(self, i: int, in_arg: bool) -> tuple[Optional[Item], int]:
        toks = self.toks
        t = toks[i]
        if t.kind == "lbrace":
            children, j = self.parse_seq(i + 1, until_rbrace=True)
            end = toks[j - 1].end if j - 1 < len(toks) and toks[j - 1].kind == "rbrace" else (children[-1].end if children else t.end)
            return Item("group", t.start, end, children=children), j
        if t.kind in ("rbrace", "amp", "sup", "sub"):
            return Item("other", t.start, t.end), i + 1
        if t.kind == "char":
            ch = t.text
            if ch.isalpha():
                return Item("atom", t.start, t.end, atom_type="id", cs=ch, base_end=t.end), i + 1
            if ch.isdigit():
                j = i + 1
                if not in_arg:
                    while j < len(toks) and toks[j].kind == "char" and (toks[j].text.isdigit() or (toks[j].text == "." and j + 1 < len(toks) and toks[j + 1].kind == "char" and toks[j + 1].text.isdigit())):
                        j += 1
                return Item("num", t.start, toks[j - 1].end), j
            return Item("other", t.start, t.end), i + 1
        # control sequence
        name = t.text
        if name == "\\\\":
            # row break, possibly with a spacing argument: \\[2pt] -- keep the dimension untouched
            j = i + 1
            end = t.end
            if j < len(toks) and toks[j].kind == "char" and toks[j].text == "[":
                k = j
                while k < len(toks) and not (toks[k].kind == "char" and toks[k].text == "]"):
                    k += 1
                if k < len(toks):
                    end = toks[k].end
                    j = k + 1
            return Item("other", t.start, end, cs=name), j
        if name in STYLE_CMDS or name in ACCENT_CMDS:
            arg, j = self.parse_arg(i + 1)
            end = arg.end if arg else t.end
            it = Item("atom", t.start, end, atom_type="styled", cs=name, base_end=end)
            if arg is not None:
                it.children = [arg]
            return it, j
        if name in OPNAME_CMDS:
            arg, j = self.parse_arg(i + 1)
            end = arg.end if arg else t.end
            it = Item("atom", t.start, end, atom_type="opname", cs=name, base_end=end)
            if arg is not None:
                it.children = [arg]
            return it, j
        if name in TEXT_CMDS:
            j = i + 1
            # optional arguments like \hspace*{..} or \textcolor{red}{x}: consume groups greedily (max 2)
            end = t.end
            count = 0
            while j < len(toks) and toks[j].kind == "lbrace" and count < 2:
                arg, j = self.parse_arg(j)
                end = arg.end if arg else end
                count += 1
                if name not in ("\\textcolor", "\\color"):
                    break
            return Item("text", t.start, end, cs=name), j
        if name in DELIM_CMDS:
            j = i + 1
            end = t.end
            if j < len(toks) and toks[j].kind in ("char", "cs"):
                end = toks[j].end
                j += 1
            return Item("other", t.start, end, cs=name), j
        if name in ("\\begin", "\\end"):
            arg, j = self.parse_arg(i + 1)
            end = arg.end if arg else t.end
            env = self.tex[arg.start + 1 : arg.end - 1] if arg and arg.kind == "group" else ""
            # column specifications and optional args: \begin{array}{ccc}, \begin{alignat}{2}
            if name == "\\begin" and env in ("array", "tabular", "alignat", "alignat*", "alignedat", "xalignat", "subarray", "matrix*", "pmatrix*", "bmatrix*", "smallmatrix*"):
                if j < len(toks) and toks[j].kind == "char" and toks[j].text == "[":
                    while j < len(toks) and not (toks[j].kind == "char" and toks[j].text == "]"):
                        j += 1
                    j += 1
                if j < len(toks) and toks[j].kind == "lbrace":
                    a2, j = self.parse_arg(j)
                    end = a2.end if a2 else end
            return Item("other", t.start, end, cs=name), j
        if name in ARG_CMDS:
            nargs = ARG_CMDS[name]
            j = i + 1
            children: list[Item] = []
            end = t.end
            if name in OPT_ARG_CMDS and j < len(toks) and toks[j].kind == "char" and toks[j].text == "[":
                # optional argument: parse its contents as a pseudo-group up to ']'
                k = j + 1
                depth = 0
                while k < len(toks):
                    tk = toks[k]
                    if tk.kind == "lbrace":
                        depth += 1
                    elif tk.kind == "rbrace":
                        depth -= 1
                    elif tk.kind == "char" and tk.text == "]" and depth == 0:
                        break
                    k += 1
                inner, _ = self.parse_seq_range(j + 1, k)
                opt = Item("group", toks[j].start, toks[k].end if k < len(toks) else toks[k - 1].end, children=inner)
                children.append(opt)
                end = opt.end
                j = k + 1
            for _ in range(nargs):
                arg, j2 = self.parse_arg(j)
                if arg is None:
                    break
                children.append(arg)
                end = arg.end
                j = j2
            return Item("cmd", t.start, end, cs=name, children=children), j
        if name in GREEK:
            return Item("atom", t.start, t.end, atom_type="greek", cs=name, base_end=t.end), i + 1
        if name in NAMED_SYMS:
            return Item("atom", t.start, t.end, atom_type="sym", cs=name, base_end=t.end), i + 1
        if name in BIGOPS:
            return Item("atom", t.start, t.end, atom_type="bigop", cs=name, base_end=t.end), i + 1
        return Item("other", t.start, t.end, cs=name), i + 1

    def parse_seq_range(self, i: int, stop: int) -> tuple[list[Item], int]:
        """Parse tokens i..stop-1 as a sequence (used for optional arguments)."""
        saved = self.toks
        self.toks = saved[:stop]
        try:
            items, j = self.parse_seq(i, until_rbrace=False)
        finally:
            self.toks = saved
        return items, j


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

@dataclass
class Unit:
    id: int
    tex: str
    key: str
    base: str
    keys: list[str]
    start: int
    end: int
    parent: Optional[int]
    atom_type: str

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tex": self.tex, "key": self.key, "base": self.base, "keys": self.keys,
            "start": self.start, "end": self.end, "parent": self.parent, "type": self.atom_type,
        }


@dataclass
class MathAnalysis:
    tex: str
    tagged: str
    units: list[Unit]


def analyze_math(tex: str, first_id: int = 1) -> MathAnalysis:
    """Find identifier units in ``tex`` and return the tagged TeX.

    ``first_id`` is the id given to the first unit; ids are consecutive so a
    caller can keep them unique across a whole paper.
    """
    try:
        parser = _Parser(tex)
        items = parser.parse()
        units: list[Unit] = []
        counter = [first_id]
        _collect_units(tex, items, None, units, counter)
        tagged = _emit(tex, items, 0, len(tex))
        return MathAnalysis(tex=tex, tagged=tagged, units=units)
    except Exception:
        return MathAnalysis(tex=tex, tagged=tex, units=[])


def _collect_units(tex: str, items: list[Item], parent: Optional[int], out: list[Unit], counter: list[int]) -> None:
    for it in items:
        if it.kind == "atom":
            uid = counter[0]
            counter[0] += 1
            it.unit_id = uid
            key, base, keys = normalize_atom(tex, it)
            out.append(Unit(uid, tex[it.start : it.end], key, base, keys, it.start, it.end, parent, it.atom_type))
            for sc in it.scripts:
                if sc.arg is not None:
                    if sc.arg.kind == "atom":
                        _collect_units(tex, [sc.arg], uid, out, counter)
                    else:
                        _collect_units(tex, sc.arg.children if sc.arg.kind in ("group", "cmd", "other") else [], uid, out, counter)
                        if sc.arg.kind == "cmd":
                            pass
        elif it.kind in ("group", "cmd", "other"):
            _collect_units(tex, it.children, parent, out, counter)
        # text / num: nothing


def _emit(tex: str, items: list[Item], start: int, end: int) -> str:
    """Re-emit ``tex[start:end]`` with ``\\class`` wrappers around atoms."""
    out: list[str] = []
    pos = start
    for it in items:
        if it.start < pos:
            continue
        out.append(tex[pos : it.start])
        out.append(_emit_item(tex, it))
        pos = it.end
    out.append(tex[pos:end])
    return "".join(out)


def _emit_item(tex: str, it: Item) -> str:
    if it.kind == "atom":
        inner = _emit_atom_inner(tex, it)
        if it.unit_id is None:
            return inner
        return f"\\class{{pa-u-{it.unit_id}}}{{{inner}}}"
    if it.kind == "group":
        inner = _emit(tex, it.children, it.start + 1, it.end - 1) if it.end - 1 > it.start else ""
        return "{" + inner + "}"
    if it.kind in ("cmd", "other") and it.children:
        return _emit(tex, it.children, it.start, it.end)
    return tex[it.start : it.end]


def _emit_atom_inner(tex: str, it: Item) -> str:
    out = [tex[it.start : it.base_end]]
    pos = it.base_end
    for sc in it.scripts:
        out.append(tex[pos : sc.op_start])
        out.append(sc.op)
        pos = sc.op_end
        if sc.arg is not None:
            out.append(tex[pos : sc.arg.start])
            arg_text = _emit_item(tex, sc.arg)
            if sc.arg.kind != "group":
                arg_text = "{" + arg_text + "}"
            out.append(arg_text)
            pos = sc.arg.end
    out.append(tex[pos : it.end])
    return "".join(out)


# ---------------------------------------------------------------------------
# Normalization (canonical keys)
# ---------------------------------------------------------------------------

def normalize_atom(tex: str, it: Item) -> tuple[str, str, list[str]]:
    base = _norm_base(tex, it)
    subs, sups, primes = [], [], 0
    for sc in it.scripts:
        if sc.op == "'":
            primes += 1
        elif sc.arg is not None:
            s = _norm_seq(tex, sc.arg)
            if s == "\\prime" or s == "{\\prime}":
                primes += 1
                continue
            (subs if sc.op == "_" else sups).append(s)
    sub = ",".join(subs) if subs else ""
    sup = ",".join(sups) if sups else ""
    prime = "'" * primes
    key = base + (f"_{{{sub}}}" if sub else "") + (f"^{{{sup}}}" if sup else "") + prime
    keys = [key]
    if sub and sup:
        keys.append(base + f"_{{{sub}}}" + prime)
        keys.append(base + f"^{{{sup}}}" + prime)
    if prime and (sub or sup):
        keys.append(base + prime)
    if base not in keys:
        keys.append(base)
    return key, base, keys


def _norm_base(tex: str, it: Item) -> str:
    if it.atom_type in ("id",):
        return it.cs
    if it.atom_type in ("greek", "sym", "bigop"):
        return it.cs
    if it.atom_type in ("styled", "opname"):
        arg = it.children[0] if it.children else None
        inner = _norm_seq(tex, arg, strip_outer=True) if arg else ""
        cmd = it.cs
        if cmd in ("\\mathop", "\\operatorname*"):
            cmd = "\\operatorname"
        if cmd == "\\operatorname":
            while True:
                m = re.fullmatch(r"\\(?:mathrm|operatorname|mathit|mathsf|text)\{(.*)\}", inner)
                if not m:
                    break
                inner = m.group(1)
            return f"\\operatorname{{{inner}}}"
        if cmd == "\\mathrm" and len(re.sub(r"[^A-Za-z]", "", inner)) > 1 and re.fullmatch(r"[A-Za-z]+", inner):
            return f"\\operatorname{{{inner}}}"
        if cmd == "\\bm":
            cmd = "\\boldsymbol"
        if cmd == "\\tilde":
            cmd = "\\widetilde"
        if cmd == "\\hat":
            cmd = "\\widehat"
        return f"{cmd}{{{inner}}}"
    return tex[it.start : it.base_end]


def _norm_seq(tex: str, item: Optional[Item], strip_outer: bool = False) -> str:
    """Canonical text for a script argument or styled-letter argument."""
    if item is None:
        return ""
    if item.kind == "group":
        parts = [_norm_item(tex, c) for c in item.children]
        s = _join(parts)
        return s if strip_outer else s
    return _norm_item(tex, item)


def _norm_item(tex: str, it: Item) -> str:
    if it.kind == "atom":
        key, _, _ = normalize_atom(tex, it)
        return key
    if it.kind == "group":
        return "{" + _join([_norm_item(tex, c) for c in it.children]) + "}"
    if it.kind == "cmd":
        return it.cs + "".join("{" + _norm_seq(tex, c, strip_outer=True) + "}" if c.kind == "group" else _norm_item(tex, c) for c in it.children)
    if it.kind == "other":
        if it.cs in SPACING_CMDS or it.cs in DELIM_CMDS and it.cs in ("\\left", "\\right"):
            # keep the delimiter after \left / \right but drop the sizing command
            rest = tex[it.start + len(it.cs) : it.end].strip()
            return rest if it.cs in ("\\left", "\\right") and rest != "." else ""
        if it.cs in DELIM_CMDS:
            return tex[it.start + len(it.cs) : it.end].strip()
        if it.cs in SPACING_CMDS:
            return ""
        if it.children:
            return tex[it.start : it.end]
        return tex[it.start : it.end].strip()
    if it.kind == "num":
        return tex[it.start : it.end]
    if it.kind == "text":
        return re.sub(r"\s+", " ", tex[it.start : it.end])
    return tex[it.start : it.end]


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not p:
            continue
        if out and re.search(r"\\[A-Za-z]+$", out) and p[0].isalpha():
            out += " "
        out += p
    return out


def normalize_tex(tex: str) -> str:
    """Canonical key for a whole small formula (used for glossary keys)."""
    try:
        items = _Parser(tex).parse()
        return _join([_norm_item(tex, it) for it in items])
    except Exception:
        return re.sub(r"\s+", "", tex)


# ---------------------------------------------------------------------------
# Character helpers used by the standard dictionary
# ---------------------------------------------------------------------------

CLASS_TAG_RE = re.compile(r"\\class\{pa-u-\d+\}\{")


def strip_tags(tagged: str) -> str:
    """Remove ``\\class{pa-u-N}{...}`` wrappers (keeping their content)."""
    out = []
    i = 0
    depth_stack: list[int] = []  # positions where a wrapper's group opened
    depth = 0
    while i < len(tagged):
        m = CLASS_TAG_RE.match(tagged, i)
        if m:
            depth_stack.append(depth)
            i = m.end()
            continue
        ch = tagged[i]
        if ch == "\\" and i + 1 < len(tagged):
            out.append(tagged[i : i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth_stack and depth_stack[-1] == depth:
                depth_stack.pop()
                i += 1
                continue
            depth -= 1
        out.append(ch)
        i += 1
    return "".join(out)
