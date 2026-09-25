"""arXiv: identify a cited paper and download its LaTeX source.

Network access goes through one injectable ``fetch(url) -> (bytes, content_type)``
so that everything here can be tested without the network.
"""
from __future__ import annotations

import difflib
import gzip
import io
import re
import tarfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Optional

from ..ingest.bib import BibEntry

Fetch = Callable[[str], tuple[bytes, str]]

USER_AGENT = "PapAssist/0.1 (reading assistant; contact via GitHub)"
ARXIV_ID_RE = re.compile(r"(?:(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(?:v\d+)?", re.I)


class ArxivError(RuntimeError):
    pass


class NoSourceError(ArxivError):
    """arXiv has no LaTeX source for this paper (PDF only)."""


def default_fetch(url: str, timeout: int = 90) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - fixed hosts
        return resp.read(), resp.headers.get("Content-Type", "") or ""


def normalize_arxiv_id(s: Optional[str]) -> Optional[str]:
    """'arXiv:2503.23482v2', 'https://arxiv.org/abs/2503.23482', 'math/0607256' -> canonical id without version."""
    if not s:
        return None
    s = s.strip()
    s = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf|e-print)/", "", s, flags=re.I)
    s = re.sub(r"^arxiv:\s*", "", s, flags=re.I)
    s = re.sub(r"^(?:abs|pdf|e-print)/", "", s, flags=re.I)
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    m = ARXIV_ID_RE.fullmatch(s)
    if not m:
        return None
    return re.sub(r"v\d+$", "", s, flags=re.I)


def _norm_title(t: str) -> str:
    t = re.sub(r"\$[^$]*\$", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def search_arxiv(title: str, first_author: Optional[str] = None, fetch: Fetch = default_fetch) -> Optional[str]:
    """Look a paper up by title (and author) through the arXiv API; return its id when the title matches."""
    if not title or len(_norm_title(title)) < 8:
        return None
    words = [w for w in _norm_title(title).split() if len(w) > 2][:12]
    q = "ti:" + "+AND+ti:".join(urllib.parse.quote(w) for w in words)
    if first_author:
        last = _norm_title(first_author).split()[-1] if _norm_title(first_author) else ""
        if last:
            q += "+AND+au:" + urllib.parse.quote(last)
    url = f"https://export.arxiv.org/api/query?search_query={q}&max_results=5"
    data, _ = fetch(url)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    want = _norm_title(title)
    best: tuple[float, Optional[str]] = (0.0, None)
    for entry in root.findall("a:entry", ns):
        t = entry.findtext("a:title", default="", namespaces=ns)
        idurl = entry.findtext("a:id", default="", namespaces=ns)
        score = difflib.SequenceMatcher(None, want, _norm_title(t)).ratio()
        if score > best[0]:
            best = (score, normalize_arxiv_id(idurl))
    return best[1] if best[0] >= 0.88 else None


def resolve_arxiv_id(entry: BibEntry, fetch: Fetch = default_fetch, allow_search: bool = True) -> tuple[Optional[str], str]:
    """(arxiv id, how) where how is 'field', 'search' or 'none'."""
    aid = normalize_arxiv_id(entry.arxiv) if entry.arxiv else None
    if aid:
        return aid, "field"
    for f in ("url", "howpublished", "note", "journal", "eprint"):
        v = entry.fields.get(f, "")
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s}]+)", v, re.I) or re.search(r"arxiv:\s*([\w./\-]+)", v, re.I)
        if m:
            aid = normalize_arxiv_id(m.group(1))
            if aid:
                return aid, "field"
    if allow_search and entry.title:
        try:
            aid = search_arxiv(entry.title, entry.authors[0] if entry.authors else None, fetch=fetch)
        except Exception:
            aid = None
        if aid:
            return aid, "search"
    return None, "none"


def _safe_members(tar: tarfile.TarFile):
    for m in tar.getmembers():
        name = m.name
        if name.startswith("/") or ".." in Path(name).parts or m.issym() or m.islnk():
            continue
        yield m


def unpack_eprint(data: bytes, dest: Path, fallback_name: str = "main.tex") -> list[Path]:
    """Write an arXiv e-print (tar, gzipped tar, gzipped single file, or plain TeX) into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    if data[:4] == b"%PDF":
        raise NoSourceError("arXiv only has a PDF for this paper (no LaTeX source).")
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise ArxivError(f"could not decompress the e-print: {e}") from e
    bio = io.BytesIO(data)
    try:
        with tarfile.open(fileobj=bio, mode="r:*") as tar:
            members = list(_safe_members(tar))
            tar.extractall(dest, members=members)
            return [dest / m.name for m in members if m.isfile()]
    except tarfile.TarError:
        pass
    # a single file: TeX source (or something else)
    if data[:4] == b"%PDF":
        raise NoSourceError("arXiv only has a PDF for this paper (no LaTeX source).")
    text = data.decode("utf-8", errors="replace")
    if "\\documentclass" not in text and "\\begin{document}" not in text:
        raise ArxivError("the e-print did not contain LaTeX source.")
    p = dest / fallback_name
    p.write_text(text, encoding="utf-8")
    return [p]


def download_eprint(arxiv_id: str, dest: Path, fetch: Fetch = default_fetch) -> list[Path]:
    aid = normalize_arxiv_id(arxiv_id)
    if not aid:
        raise ArxivError(f"not an arXiv identifier: {arxiv_id!r}")
    data, ctype = fetch(f"https://arxiv.org/e-print/{aid}")
    if "pdf" in ctype.lower() and data[:4] == b"%PDF":
        raise NoSourceError("arXiv only has a PDF for this paper (no LaTeX source).")
    return unpack_eprint(data, dest, fallback_name=aid.replace("/", "_") + ".tex")


def arxiv_metadata(arxiv_id: str, fetch: Fetch = default_fetch) -> dict:
    """Title and authors from the arXiv API (best effort)."""
    aid = normalize_arxiv_id(arxiv_id)
    if not aid:
        return {}
    try:
        data, _ = fetch(f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(aid)}")
        root = ET.fromstring(data)
    except Exception:
        return {}
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return {}
    title = re.sub(r"\s+", " ", entry.findtext("a:title", default="", namespaces=ns)).strip()
    authors = [a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)]
    return {"title": title, "authors": [a for a in authors if a], "arxiv": aid}


def directory_fetch(folder: Path) -> Fetch:
    """A fake network for tests and demos: every e-print request returns ``folder/eprint.tar.gz``
    (or ``folder/eprint-<id>.tar.gz`` when present) and every API query returns ``folder/api.xml``."""
    folder = Path(folder)

    def fetch(url: str) -> tuple[bytes, str]:
        if "/e-print/" in url:
            aid = url.rsplit("/e-print/", 1)[1].replace("/", "_")
            for cand in (folder / f"eprint-{aid}.tar.gz", folder / "eprint.tar.gz"):
                if cand.exists():
                    return cand.read_bytes(), "application/x-eprint-tar"
            raise ArxivError(f"fake arXiv: no e-print for {aid}")
        if "api/query" in url:
            p = folder / "api.xml"
            return (p.read_bytes() if p.exists() else b"<feed xmlns='http://www.w3.org/2005/Atom'></feed>"), "application/atom+xml"
        raise ArxivError(f"fake arXiv: unexpected url {url}")

    return fetch
