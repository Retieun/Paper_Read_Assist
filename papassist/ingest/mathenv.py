"""Prepare display math for MathJax: equation environments, labels, numbering."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

AMS_NUMBERED = {"equation", "align", "gather", "multline", "eqnarray", "alignat", "flalign"}
AMS_ENVS = AMS_NUMBERED | {e + "*" for e in AMS_NUMBERED}

_BEGIN_RE = re.compile(r"^\s*\\begin\{([a-zA-Z*]+)\}(\s*\{[^}]*\})?", re.S)
_LABEL_RE = re.compile(r"\\label\s*\{([^}]*)\}")
_TAG_RE = re.compile(r"\\tag\*?\s*\{([^}]*)\}")
_NOTAG_RE = re.compile(r"\\(?:notag|nonumber)\b")


@dataclass
class PreparedMath:
    tex: str                 # clean TeX (labels removed) for text/LLM use
    render: str              # TeX to hand MathJax (with \tag{n} added)
    bare: bool               # render already is a top-level environment
    labels: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    label_numbers: dict[str, str] = field(default_factory=dict)


def split_rows(body: str) -> list[str]:
    """Split on top-level ``\\\\`` (not inside braces or nested environments)."""
    rows: list[str] = []
    depth = 0
    env_depth = 0
    i = 0
    cur = []
    while i < len(body):
        if body.startswith("\\begin", i):
            env_depth += 1
        elif body.startswith("\\end", i):
            env_depth -= 1
        if body[i] == "\\" and i + 1 < len(body):
            two = body[i : i + 2]
            if two == "\\\\" and depth == 0 and env_depth <= 0:
                rows.append("".join(cur))
                cur = []
                i += 2
                # optional spacing argument \\[2pt]
                m = re.match(r"\s*\[[^\]]*\]", body[i:])
                if m:
                    i += m.end()
                continue
            cur.append(two)
            i += 2
            continue
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
        cur.append(body[i])
        i += 1
    rows.append("".join(cur))
    return rows


def join_rows(rows: list[str]) -> str:
    """Join rows with ``\\\\``; a row starting with ``[`` gets ``{}`` first so the bracket is not read as a dimension."""
    out = []
    for i, r in enumerate(rows):
        if i > 0 and r.lstrip().startswith("["):
            r = "{}" + r
        out.append(r)
    return "\\\\".join(out)


def prepare_display_math(tex: str, next_number: Callable[[], str]) -> PreparedMath:
    """Normalise one display formula.

    ``next_number`` hands out the next equation number each time it is called.
    """
    labels_all: list[str] = []
    numbers_all: list[str] = []
    label_numbers: dict[str, str] = {}

    m = _BEGIN_RE.match(tex)
    env = m.group(1) if m else None
    if env in AMS_ENVS:
        head = m.group(0)
        end_re = re.compile(r"\\end\{" + re.escape(env) + r"\}\s*$", re.S)
        em = end_re.search(tex)
        body = tex[len(head) : em.start()] if em else tex[len(head) :]
        base = env.rstrip("*")
        numbered = env in AMS_NUMBERED

        if base == "equation":
            labels = _LABEL_RE.findall(body)
            body_clean = _LABEL_RE.sub("", body)
            tag = _TAG_RE.search(body_clean)
            if numbered and not tag and not _NOTAG_RE.search(body_clean):
                n = next_number()
                body_render = body_clean.rstrip() + f"\\tag{{{n}}}"
                numbers_all.append(n)
                for l in labels:
                    label_numbers[l] = n
            else:
                body_render = _NOTAG_RE.sub("", body_clean)
                if tag:
                    for l in labels:
                        label_numbers[l] = tag.group(1)
                    numbers_all.append(tag.group(1))
            labels_all.extend(labels)
            return PreparedMath(
                tex=body_clean.strip(), render="\\[" + body_render.strip() + "\\]", bare=False,
                labels=labels_all, numbers=numbers_all, label_numbers=label_numbers,
            )

        # multi-row environments
        rows = split_rows(body)
        out_rows = []
        clean_rows = []
        for idx, row in enumerate(rows):
            labels = _LABEL_RE.findall(row)
            row_clean = _LABEL_RE.sub("", row)
            labels_all.extend(labels)
            is_last = idx == len(rows) - 1
            if not row_clean.strip() and is_last:
                clean_rows.append(row_clean)
                out_rows.append(row_clean)
                continue
            tag = _TAG_RE.search(row_clean)
            if numbered and base != "multline" and not tag and not _NOTAG_RE.search(row_clean):
                n = next_number()
                numbers_all.append(n)
                for l in labels:
                    label_numbers[l] = n
                out_rows.append(row_clean.rstrip() + f"\\tag{{{n}}}")
            elif numbered and base == "multline" and is_last and not tag:
                n = next_number()
                numbers_all.append(n)
                for l in labels:
                    label_numbers[l] = n
                out_rows.append(row_clean.rstrip() + f"\\tag{{{n}}}")
            else:
                if tag:
                    numbers_all.append(tag.group(1))
                    for l in labels:
                        label_numbers[l] = tag.group(1)
                out_rows.append(_NOTAG_RE.sub("", row_clean))
            clean_rows.append(_NOTAG_RE.sub("", row_clean))
        # multline labels attach to the single number
        if base == "multline" and numbers_all:
            for l in labels_all:
                label_numbers.setdefault(l, numbers_all[0])
        render = head + join_rows(out_rows) + f"\\end{{{env}}}"
        clean = head + join_rows(clean_rows) + f"\\end{{{env}}}"
        return PreparedMath(tex=clean.strip(), render=render, bare=True, labels=labels_all,
                            numbers=numbers_all, label_numbers=label_numbers)

    # plain \[ ... \] content (may contain aligned, cases, split ...)
    labels = _LABEL_RE.findall(tex)
    clean = _LABEL_RE.sub("", tex)
    tag = _TAG_RE.search(clean)
    if tag:
        numbers_all.append(tag.group(1))
        for l in labels:
            label_numbers[l] = tag.group(1)
    labels_all.extend(labels)
    return PreparedMath(tex=clean.strip(), render="\\[" + clean.strip() + "\\]", bare=False,
                        labels=labels_all, numbers=numbers_all, label_numbers=label_numbers)
