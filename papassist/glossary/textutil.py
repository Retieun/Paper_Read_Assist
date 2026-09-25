"""Small text helpers shared by the extractors."""
from __future__ import annotations

import re

PH_RE = re.compile(r"⟦(m\d+)⟧")
PH = r"⟦(?:m\d+)⟧"

_ABBREV = ("e.g.", "i.e.", "cf.", "resp.", "vs.", "Thm.", "Def.", "Lem.", "Prop.", "Cor.", "Sec.", "Fig.", "Eq.", "et al.", "viz.", "No.", "pp.", "p.", "Ch.", "Rem.", "Ex.")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of sentences in ``text``.

    Splits on '.', '!', '?' followed by whitespace and an uppercase letter,
    a placeholder, or the end of the text; keeps common abbreviations intact.
    """
    spans: list[tuple[int, int]] = []
    start = 0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in ".!?":
            tail = text[max(0, i - 8) : i + 1]
            if any(tail.endswith(a) for a in _ABBREV):
                i += 1
                continue
            # a decimal like 3.3 or a label like Definition 3.3
            if ch == "." and i + 1 < n and text[i + 1].isdigit():
                i += 1
                continue
            j = i + 1
            while j < n and text[j] in ")\"'”’":
                j += 1
            k = j
            while k < n and text[k] in " \t\n ":
                k += 1
            if k >= n or text[k].isupper() or text[k] == "⟦" or text[k] in "(\"“" or text[k].isdigit() or (k > j and text[k] == "\n"):
                spans.append((start, j))
                start = k
                i = k
                continue
        if ch == "\n" and i + 1 < n and text[i + 1] == "\n":
            spans.append((start, i))
            start = i + 2
            i += 2
            continue
        i += 1
    if start < n:
        spans.append((start, n))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def words_only(term: str) -> str:
    """Lower-case a term and drop inline math, keeping the words."""
    t = re.sub(r"\$[^$]*\$", " ", term)
    t = re.sub(r"[^\w\s\-']", " ", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    t = t.strip("-' ")
    return t


def singularize(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if word.endswith("ices") and len(word) > 5:  # matrices -> matrix, vertices -> vertex
        return word[:-4] + ("ex" if word.endswith(("rtices", "tices")) else "ix")
    if word.endswith("s") and not word.endswith("ss") and not word.endswith("us"):
        return word[:-1]
    return word


def pluralize(word: str) -> list[str]:
    outs = []
    if word.endswith("y") and len(word) > 2 and word[-2] not in "aeiou":
        outs.append(word[:-1] + "ies")
    elif word.endswith(("s", "x", "ch", "sh")):
        outs.append(word + "es")
    elif word.endswith("ex") or word.endswith("ix"):
        outs.append(word[:-2] + "ices")
        outs.append(word + "es")
    else:
        outs.append(word + "s")
    return outs


def term_variants(term_words: str) -> list[str]:
    """Surface forms to match in text for a term given as lower-case words."""
    words = term_words.split()
    if not words:
        return []
    forms = {term_words}
    last = words[-1]
    base = singularize(last)
    for form in {last, base, *pluralize(base)}:
        forms.add(" ".join(words[:-1] + [form]))
    out = set()
    for f in forms:
        out.add(f)
        out.add(f.replace("-", " "))
        out.add(f.replace(" ", "-"))
    return sorted(out, key=len, reverse=True)


def canonical_term(term: str) -> str:
    """Canonical key for a term: lower-case words, singular last word, spaces for hyphens."""
    w = words_only(term)
    w = w.replace("-", " ")
    parts = w.split()
    if not parts:
        return ""
    parts[-1] = singularize(parts[-1])
    return " ".join(parts)
