from papassist.ingest.preamble import parse_preamble


def test_preamble_macros_and_theorems():
    pre = parse_preamble(r"""
    \documentclass{amsart}
    \newtheorem{thm}{Theorem}[section]
    \newtheorem{defn}[thm]{Definition}
    \theoremstyle{remark}\newtheorem*{rem}{Remark}
    \newcommand{\Hloc}[2]{H^{#1}_{#2}}
    \newcommand\PH{\mathrm{PH}}
    \DeclareMathOperator*{\colim}{colim}
    \def\foo#1{#1+1}
    \numberwithin{equation}{section}
    \bibliography{a,b}
    """)
    assert pre.documentclass == "amsart"
    assert pre.theorems["defn"].counter == "thm" and pre.theorems["defn"].reset_by == "section"
    assert pre.theorems["rem"].numbered is False and pre.theorems["rem"].style == "remark"
    assert pre.theorems["defn"].kind == "definition"
    assert pre.macros["Hloc"].nargs == 2 and pre.macros["Hloc"].body == "H^{#1}_{#2}"
    assert pre.macros["PH"].body == r"\mathrm{PH}"
    assert pre.macros["colim"].body == r"\operatorname*{colim}"
    assert pre.macros["foo"].nargs == 1
    assert pre.equation_reset == "section"
    assert pre.bib_files == ["a", "b"]


def blocks_of(doc, kind):
    return [b for b in doc.blocks if b.kind == kind]


def test_document_structure(sample_doc):
    doc = sample_doc
    assert doc.title == "A Sample Paper on Persistence"
    assert doc.authors == ["Ada Lovelace", "Emmy Noether"]
    kinds = {b.kind for b in doc.blocks}
    assert {"title", "abstract", "heading", "para", "theorem", "proof", "figure"} <= kinds
    assert not doc.warnings, doc.warnings


def test_theorem_numbering_follows_the_declarations(sample_doc):
    thms = {b.label: (b.thm_title, b.number) for b in sample_doc.blocks if b.kind == "theorem" and b.label}
    assert thms["def:pm"] == ("Definition", "2.1")            # first theorem-like item in section 2
    assert thms["def:interleave"] == ("Definition", "2.2")
    assert thms["thm:stab"] == ("Theorem", "3.1")              # counter reset by the section
    assert thms["lem:cases"] == ("Lemma", "3.2")               # lemma shares the theorem counter
    conv = next(b for b in sample_doc.blocks if b.env == "convention")
    assert conv.number is None                                 # starred environment


def test_titles_and_headings(sample_doc):
    b = next(b for b in sample_doc.blocks if b.label == "def:pm")
    assert b.title == "Persistence module"
    assert b.heading == "Definition 2.1 (Persistence module)"
    assert b.text.startswith("Definition 2.1 (Persistence module). Let $(P,\\le)$ be a poset.")
    ack = next(b for b in sample_doc.blocks if b.kind == "heading" and "Acknowledgements" in b.text)
    assert ack.number is None
    stab = next(b for b in sample_doc.blocks if b.label == "sec:stab")
    assert stab.number == "3"


def test_equation_numbers_and_refs(sample_doc):
    labels = sample_doc.labels
    assert labels["eq:sr"]["number"] == "1"
    assert labels["eq:di"]["number"] == "2"          # the \notag row gets no number
    para = next(b for b in sample_doc.blocks if "contains" in b.text and "vertex prime" in b.text)
    assert "by (1)." in para.text
    proof = next(b for b in sample_doc.blocks if b.kind == "proof")
    assert "by Definition 2.1" in proof.text.replace("\xa0", " ")
    assert "§1" in proof.text or "1." in proof.text
    abstract = next(b for b in sample_doc.blocks if b.kind == "abstract")
    assert "Theorem 3.1" in abstract.text.replace("\xa0", " ")


def test_display_math_preparation(sample_doc):
    di = next(m for m in sample_doc.math.values() if "eq:di" in m.labels)
    assert di.bare and di.tagged.startswith(r"\begin{align}") and r"\tag{2}" in di.tagged
    assert r"\label" not in di.tagged
    sr = next(m for m in sample_doc.math.values() if "eq:sr" in m.labels)
    assert sr.tagged.startswith(r"\[") and r"\tag{1}" in sr.tagged


def test_citations_are_rendered_with_bibliography(sample_doc):
    proof = next(b for b in sample_doc.blocks if b.kind == "proof")
    text = proof.text.replace("\xa0", " ")
    assert "[1, Thm. 4.4]" in text                      # plain style: sorted by author, Bauer before Chazal
    assert proof.cites[0]["key"] == "bauer2015induced"
    d = next(b for b in sample_doc.blocks if b.label == "def:pm")
    assert "following [2, Definition 2.1]" in d.text.replace("\xa0", " ")
    assert 'href="#lbl-bib-bauer2015induced"' in proof.html
    bib = next(b for b in sample_doc.blocks if b.kind == "bibliography")
    assert bib is sample_doc.blocks[-1]
    assert 'id="lbl-bib-chazal2016structure"' in bib.html and "[2]" in bib.html and "arXiv:1207.3674" in bib.html
    assert "Induced matchings" in bib.text
    assert sample_doc.bibliography["chazal2016structure"]["arxiv"] == "1207.3674"
    assert sample_doc.bibliography["bauer2015induced"]["doi"] == "10.20382/jocg.v6i2a9"


def test_diagrams_become_placeholders(sample_doc):
    assert len(sample_doc.diagrams) == 2
    assert any("Diagram" in b.html and "pa-diagram" in b.html for b in sample_doc.blocks)
    assert not any("tikzcd" in m.tex for m in sample_doc.math.values())
    fig = next(b for b in sample_doc.blocks if b.kind == "figure")
    assert fig.number == "1" and "A triangle." in fig.text


def test_row_spacing_survives_tagging(sample_doc):
    lem = next(m for m in sample_doc.math.values() if "cases" in m.tex)
    assert r"\\[2pt]" in lem.tagged


def test_emphasis_and_footnotes(sample_doc):
    d = next(b for b in sample_doc.blocks if b.label == "def:pm")
    assert "$P$-indexed persistence module" in d.emph and "pointwise finite-dimensional" in d.emph
    ack = next(b for b in sample_doc.blocks if "Thanks to everyone" in b.text)
    assert "footnote: Really everyone." in ack.text and "pa-fn" in ack.html
