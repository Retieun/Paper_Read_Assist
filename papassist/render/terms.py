"""Wrap known terms in the rendered HTML so they can be hovered."""
from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser
from typing import Optional

VOID = {"br", "img", "hr", "input", "meta", "link", "wbr", "col", "area", "base", "source", "track", "embed", "param"}
SKIP_TAGS = {"a", "code", "pre", "script", "style", "h1", "h2", "h3", "h4", "h5", "h6", "sup", "details", "summary"}
SKIP_CLASSES = ("pa-math", "pa-cite", "pa-thm-head", "pa-fn", "pa-ref", "pa-diagram", "pa-secnum", "pa-term", "pa-authors", "pa-title")


class TermMatcher:
    """Compiled matcher for a set of term variants -> canonical key."""

    def __init__(self, variants: dict[str, tuple[str, str]]):
        # variants: surface form (lower) -> (key, status)
        self.variants = variants
        forms = sorted(variants.keys(), key=len, reverse=True)
        if forms:
            alt = "|".join(re.escape(f).replace(r"\ ", r"[\s\u00a0\-\u2013\u2014]+").replace(r"\-", r"[\-\u2013\u2014\s]") for f in forms)
            self.re = re.compile(rf"(?<![\w\-])({alt})(?![\w\-])", re.I)
        else:
            self.re = None

    def lookup(self, surface: str) -> Optional[tuple[str, str]]:
        s = re.sub(r"[\u2013\u2014]", "-", surface.lower())
        s = re.sub(r"[\s\u00a0]+", " ", s).strip()
        hit = self.variants.get(s) or self.variants.get(s.replace(" ", "-")) or self.variants.get(s.replace("-", " "))
        return hit

    def wrap_text(self, text: str, def_key: Optional[str] = None) -> str:
        """Escape ``text`` and wrap term occurrences in spans."""
        if self.re is None:
            return htmllib.escape(text, quote=False)
        out = []
        pos = 0
        for m in self.re.finditer(text):
            hit = self.lookup(m.group(1))
            out.append(htmllib.escape(text[pos:m.start()], quote=False))
            if hit is None:
                out.append(htmllib.escape(m.group(1), quote=False))
            else:
                key, status = hit
                cls = "pa-term" + (" pa-term-def" if def_key == key else "") + (" pa-term-undef" if status == "undefined" else "")
                out.append(f'<span class="{cls}" data-term="{htmllib.escape(key, quote=True)}">{htmllib.escape(m.group(1), quote=False)}</span>')
            pos = m.end()
        out.append(htmllib.escape(text[pos:], quote=False))
        return "".join(out)


class _Wrapper(HTMLParser):
    def __init__(self, matcher: TermMatcher, def_key: Optional[str]):
        super().__init__(convert_charrefs=True)
        self.matcher = matcher
        self.def_key = def_key
        self.out: list[str] = []
        self.skip_depth = 0
        self.stack: list[tuple[str, bool]] = []  # (tag, skipping)

    def _attrs(self, attrs) -> str:
        parts = []
        for k, v in attrs:
            if v is None:
                parts.append(f" {k}")
            else:
                parts.append(f' {k}="{htmllib.escape(v, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class", "") or ""
        skipping = tag in SKIP_TAGS or any(c in cls for c in SKIP_CLASSES)
        self.out.append(f"<{tag}{self._attrs(attrs)}>")
        if tag in VOID:
            return
        self.stack.append((tag, skipping))
        if skipping:
            self.skip_depth += 1

    def handle_startendtag(self, tag, attrs):
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # pop to the matching tag (tolerate malformed nesting)
        while self.stack:
            t, skipping = self.stack.pop()
            if skipping:
                self.skip_depth -= 1
            if t == tag:
                break
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.skip_depth > 0:
            self.out.append(htmllib.escape(data, quote=False))
        else:
            self.out.append(self.matcher.wrap_text(data, self.def_key))

    def handle_entityref(self, name):
        self.out.append(f"&{name};")

    def handle_charref(self, name):
        self.out.append(f"&#{name};")

    def handle_comment(self, data):
        pass


def wrap_terms_html(html: str, matcher: TermMatcher, def_key: Optional[str] = None) -> str:
    if matcher.re is None or not html:
        return html
    w = _Wrapper(matcher, def_key)
    try:
        w.feed(html)
        w.close()
    except Exception:
        return html
    return "".join(w.out)
