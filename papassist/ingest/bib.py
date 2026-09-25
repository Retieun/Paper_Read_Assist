"""Bibliography parsing: .bib files (bibtexparser) and .bbl / thebibliography blocks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    from pylatexenc.latex2text import LatexNodes2Text

    _L2T = LatexNodes2Text(math_mode="verbatim")

    def latex_to_text(s: str) -> str:
        try:
            return _L2T.latex_to_text(s).strip()
        except Exception:
            return re.sub(r"[{}]", "", s).strip()

except Exception:  # pragma: no cover

    def latex_to_text(s: str) -> str:
        return re.sub(r"[{}]", "", s).strip()


ARXIV_RE = re.compile(r"arxiv[:\s/]*(?:abs/)?((?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(v\d+)?", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;}\"']+)")


@dataclass
class BibEntry:
    key: str
    entry_type: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: Optional[str] = None
    venue: str = ""
    arxiv: Optional[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def short(self) -> str:
        """A short in-text label like ``Rahimi 2007`` or ``Bruns–Herzog 1998``."""
        names = [_last_name(a) for a in self.authors if a.strip()]
        if not names:
            base = self.key
        elif len(names) == 1:
            base = names[0]
        elif len(names) == 2:
            base = f"{names[0]}–{names[1]}"
        else:
            base = f"{names[0]} et al."
        return f"{base} {self.year}" if self.year else base

    def to_dict(self) -> dict:
        d = asdict(self)
        d["short"] = self.short
        return d


def _last_name(author: str) -> str:
    a = author.strip()
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    if not parts:
        return a
    # keep particles like "de Silva", "van der Waerden"
    i = len(parts) - 1
    while i > 0 and parts[i - 1].islower() and len(parts[i - 1]) <= 3:
        i -= 1
    return " ".join(parts[i:])


def _extract_ids(entry: BibEntry) -> None:
    blob = " ".join(entry.fields.values())
    if not entry.arxiv:
        ep = entry.fields.get("eprint")
        if ep and entry.fields.get("archiveprefix", "").lower() == "arxiv":
            entry.arxiv = ep.strip()
        elif ep and re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", ep.strip()):
            entry.arxiv = ep.strip()
    if not entry.arxiv:
        m = ARXIV_RE.search(blob)
        if m:
            entry.arxiv = m.group(1)
    if not entry.doi:
        d = entry.fields.get("doi")
        if d:
            entry.doi = d.strip()
        else:
            m = DOI_RE.search(blob)
            if m:
                entry.doi = m.group(1).rstrip(".")
    if not entry.url and entry.fields.get("url"):
        entry.url = entry.fields["url"].strip()


def parse_bib_file(path: Path) -> dict[str, BibEntry]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_bib_text(text)


def parse_bib_text(text: str) -> dict[str, BibEntry]:
    entries: dict[str, BibEntry] = {}
    try:
        import bibtexparser  # type: ignore

        lib = bibtexparser.parse_string(text)
        for e in lib.entries:
            fields = {k.lower(): (v.value if hasattr(v, "value") else str(v)) for k, v in e.fields_dict.items()}
            entries[e.key] = _make_entry(e.key, e.entry_type, fields)
    except Exception:
        entries = {}
    if not entries:
        entries = _fallback_parse(text)
    return entries


def _make_entry(key: str, etype: str, fields: dict[str, str]) -> BibEntry:
    fields = {k: str(v) for k, v in fields.items()}
    authors_raw = fields.get("author", "") or fields.get("editor", "")
    authors = [latex_to_text(a) for a in re.split(r"\s+and\s+", authors_raw) if a.strip()]
    year = fields.get("year")
    ym = re.search(r"\d{4}", year or "") or re.search(r"\d{4}", fields.get("date", ""))
    venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or fields.get("howpublished") or ""
    entry = BibEntry(
        key=key,
        entry_type=etype.lower(),
        title=latex_to_text(fields.get("title", "")),
        authors=authors,
        year=ym.group(0) if ym else None,
        venue=latex_to_text(venue),
        fields=fields,
    )
    _extract_ids(entry)
    return entry


_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.S)


def _fallback_parse(text: str) -> dict[str, BibEntry]:
    """A small regex parser used when bibtexparser is missing or chokes."""
    entries: dict[str, BibEntry] = {}
    for m in _ENTRY_RE.finditer(text):
        etype, key = m.group(1), m.group(2)
        if etype.lower() in ("string", "preamble", "comment"):
            continue
        # body: from after the key to the matching closing brace
        i = m.end()
        depth = 1
        j = i
        while j < len(text) and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[i : j - 1]
        fields: dict[str, str] = {}
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            name = fm.group(1).lower()
            k = fm.end()
            if k < len(body) and body[k] == "{":
                depth = 0
                l = k
                while l < len(body):
                    if body[l] == "{":
                        depth += 1
                    elif body[l] == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    l += 1
                fields[name] = body[k + 1 : l]
            elif k < len(body) and body[k] == '"':
                l = body.find('"', k + 1)
                fields[name] = body[k + 1 : l if l > 0 else len(body)]
            else:
                l = k
                while l < len(body) and body[l] not in ",\n":
                    l += 1
                fields[name] = body[k:l].strip()
        entries[key] = _make_entry(key, etype, fields)
    return entries


BIBITEM_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})", re.S)


def parse_bbl_text(text: str) -> dict[str, BibEntry]:
    """Parse ``\\bibitem`` entries from a .bbl file or a thebibliography block."""
    entries: dict[str, BibEntry] = {}
    for m in BIBITEM_RE.finditer(text):
        key, body = m.group(1).strip(), m.group(2)
        plain = latex_to_text(re.sub(r"\\newblock", " ", body))
        plain = re.sub(r"\s+", " ", plain).strip()
        year = re.search(r"\b(19|20)\d{2}\b", plain)
        entry = BibEntry(key=key, entry_type="bibitem", title=plain[:200], fields={"raw": plain})
        # guess authors: text before the first period-separated title chunk
        head = plain.split(".")[0] if "." in plain else ""
        if head and len(head) < 120:
            entry.authors = [a.strip() for a in re.split(r",\s*| and ", head) if a.strip()]
        entry.year = year.group(0) if year else None
        _extract_ids(entry)
        entries[key] = entry
    return entries


def load_bibliography(main_tex: Path, bib_names: list[str]) -> dict[str, BibEntry]:
    """Load all bibliography sources next to a paper: named .bib files, any .bib in
    the folder, a .bbl with the paper's name, and inline thebibliography blocks."""
    folder = main_tex.parent
    entries: dict[str, BibEntry] = {}
    candidates: list[Path] = []
    for name in bib_names:
        p = folder / name
        if p.suffix == "":
            p = p.with_suffix(".bib")
        candidates.append(p)
    candidates.extend(sorted(folder.glob("*.bib")))
    seen = set()
    for p in candidates:
        if p in seen or not p.exists():
            continue
        seen.add(p)
        try:
            entries.update(parse_bib_file(p))
        except Exception:
            continue
    for p in [main_tex.with_suffix(".bbl"), *sorted(folder.glob("*.bbl"))]:
        if p.exists():
            try:
                for k, v in parse_bbl_text(p.read_text(encoding="utf-8", errors="replace")).items():
                    entries.setdefault(k, v)
            except Exception:
                continue
    try:
        from .preamble import gather_sources, strip_comments

        for _, text in gather_sources(main_tex):
            if "\\bibitem" in text:
                for k, v in parse_bbl_text(strip_comments(text)).items():
                    entries.setdefault(k, v)
    except Exception:
        pass
    return entries
