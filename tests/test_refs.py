import gzip
import io
import tarfile
from pathlib import Path

import pytest

from papassist.ingest.bib import BibEntry
from papassist.library.store import Library
from papassist.refs.arxiv import NoSourceError, normalize_arxiv_id, resolve_arxiv_id, search_arxiv, unpack_eprint
from papassist.refs.lookup import find_block_by_pointer, parse_pointer, retag_html
from papassist.refs.service import lookup_in_library, references_status

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper"


def fixture_tarball() -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w:gz") as tar:
        for p in FIXTURE.rglob("*"):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(FIXTURE)))
    return bio.getvalue()


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/abs/1311.3681v3</id><title>Induced matchings and the algebraic stability of persistence barcodes</title><author><name>Ulrich Bauer</name></author></entry>
  <entry><id>http://arxiv.org/abs/9999.00001v1</id><title>Something else entirely</title></entry>
</feed>"""


def fake_fetch_factory(calls):
    def fetch(url):
        calls.append(url)
        if "/e-print/" in url:
            return fixture_tarball(), "application/x-eprint-tar"
        if "api/query" in url:
            return ATOM.encode(), "application/atom+xml"
        raise AssertionError(url)
    return fetch


def test_normalize_ids():
    assert normalize_arxiv_id("arXiv:2503.23482v2") == "2503.23482"
    assert normalize_arxiv_id("https://arxiv.org/abs/math/0607256") == "math/0607256"
    assert normalize_arxiv_id("abs/2507.01303") == "2507.01303"
    assert normalize_arxiv_id("not an id") is None


def test_unpack_variants(tmp_path):
    files = unpack_eprint(fixture_tarball(), tmp_path / "a")
    assert any(f.name == "main.tex" for f in files)
    single = gzip.compress(b"\\documentclass{article}\\begin{document}hi\\end{document}")
    files = unpack_eprint(single, tmp_path / "b", fallback_name="x.tex")
    assert files[0].name == "x.tex" and "documentclass" in files[0].read_text()
    with pytest.raises(NoSourceError):
        unpack_eprint(b"%PDF-1.5 ...", tmp_path / "c")


def test_resolve_from_fields_and_search():
    calls = []
    e = BibEntry(key="c", entry_type="book", title="The Structure and Stability of Persistence Modules", authors=["F. Chazal"], fields={"note": "arXiv:1207.3674"})
    e.arxiv = "1207.3674"
    assert resolve_arxiv_id(e, fetch=fake_fetch_factory(calls)) == ("1207.3674", "field")
    b = BibEntry(key="b", entry_type="article", title="Induced matchings and the algebraic stability of persistence barcodes", authors=["U. Bauer", "M. Lesnick"])
    assert resolve_arxiv_id(b, fetch=fake_fetch_factory(calls)) == ("1311.3681", "search")
    assert any("ti:induced" in u.lower() and "au:bauer" in u.lower() for u in calls)
    assert search_arxiv("A completely different title about knots", fetch=fake_fetch_factory(calls)) is None
    assert resolve_arxiv_id(BibEntry(key="z", entry_type="book", title="Elements of Algebraic Topology"), fetch=fake_fetch_factory(calls))[1] in ("none", "search")


def test_pointers(sample_doc):
    assert parse_pointer("Thm.~4.4") == [{"kind": "theorem", "number": "4.4"}]
    assert parse_pointer("§3.2, Def. 2.1")[1] == {"kind": "definition", "number": "2.1"}
    assert find_block_by_pointer(sample_doc, {"kind": "definition", "number": "2.1"}).label == "def:pm"
    assert find_block_by_pointer(sample_doc, {"kind": "theorem", "number": "3.1"}).label == "thm:stab"
    assert find_block_by_pointer(sample_doc, {"kind": "section", "number": "2"}).kind == "heading"
    assert find_block_by_pointer(sample_doc, {"kind": "equation", "number": "1"}) is not None
    assert find_block_by_pointer(sample_doc, {"kind": "theorem", "number": "9.9"}) is None


def test_retag_html():
    h = '<span class="pa-math">\\(\\class{pa-u-12}{M}\\)</span> <span class="pa-term" data-term="x">t</span>'
    out = retag_html(h, "pabc")
    assert "pa-u-xpabc_12" in out and 'data-paper="pabc" class="pa-term"' in out
    assert retag_html(out, "pabc") == out  # idempotent


def test_library_fetch_and_lookup(tmp_path):
    calls = []
    fetch = fake_fetch_factory(calls)
    lib = Library(tmp_path / "lib")
    main = lib.add([FIXTURE])
    refs = references_status(lib, main)
    chazal = next(r for r in refs if r["key"] == "chazal2016structure")
    assert chazal["arxiv"] == "1207.3674" and chazal["in_library"] is None and chazal["label"] == "2"
    # fetch the cited paper (the fixture stands in for it) and link it
    other = lib.add_from_arxiv("1207.3674", fetch=fetch, cited_by=main.id)
    assert other.meta["role"] == "reference" and other.meta["arxiv"] == "1207.3674" and other.meta["cited_by"] == [main.id]
    assert lib.find_by_arxiv("1207.3674").id == other.id
    assert lib.add_from_arxiv("1207.3674v9", fetch=fetch).id == other.id       # cached, no second download
    assert sum("/e-print/" in u for u in calls) == 1
    refs = references_status(lib, main)
    assert next(r for r in refs if r["key"] == "chazal2016structure")["in_library"] == other.id

    from papassist.resolve.resolver import Resolver

    resolvers = {}

    def get_resolver(pid):
        if pid not in resolvers:
            p = lib.get(pid)
            resolvers[pid] = Resolver(p.doc, p.glossary)
        return resolvers[pid]

    hits = lookup_in_library(lib, main, get_resolver, term="persistence module")
    assert hits and hits[0]["paper"] == other.id and hits[0]["label"] == "2"
    assert hits[0]["why"] in ("defined here", "cited location")
    assert "pa-u-x" + other.id in hits[0]["html"] and all(v[4] == other.id for v in hits[0]["units"].values())
    # the citation pointer 'Definition 2.1' points at the same block the glossary found
    from papassist.refs.lookup import find_block_by_pointer

    assert hits[0]["block"] == find_block_by_pointer(other.doc, {"kind": "definition", "number": "2.1"}).id
    sym = lookup_in_library(lib, main, get_resolver, keys=["k"])
    assert sym and "field" in sym[0]["meaning_text"] and sym[0]["paper"] == other.id
