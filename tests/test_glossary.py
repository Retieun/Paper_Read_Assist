def sym(glossary, key):
    return [s for s in glossary.symbols if s["key"] == key or key in s.get("keys", [])]


def test_let_be_and_write_for(sample_glossary):
    k = sym(sample_glossary, "k")
    assert any(s["meaning"] == "field" and s["pattern"] == "let_be" for s in k)
    dw = sym(sample_glossary, "\\Delta_{W}")
    assert any(s["meaning"].startswith("induced subcomplex") and "given by" in s["meaning"] for s in dw)


def test_term_with_symbol_and_definitions(sample_glossary):
    pi = sym(sample_glossary, "\\mathfrak{p}_{i}")
    assert any(s["pattern"] == "term_symbol" and "vertex prime" in s["meaning"] for s in pi)
    terms = {t["term"]: t for t in sample_glossary.terms}
    assert terms["vertex prime"]["source"] == "paper_inline"
    assert terms["stanley reisner ideal"]["source"] == "paper_inline"
    pm = terms["indexed persistence module"]
    assert pm["source"] == "paper_definition" and "persistence module" in pm["aliases"]
    assert terms["pointwise finite dimensional"]["aliases"] == ["pfd"]
    assert "interleaving distance" in terms                      # from the definition's title


def test_defined_as_and_map_signature(sample_glossary):
    di = sym(sample_glossary, "d_{I}(M,N)") + sym(sample_glossary, "d_{I}")
    assert any(s["pattern"] == "defined_as" for s in di)
    phi = sym(sample_glossary, "\\varphi_{M}(s,t)") + sym(sample_glossary, "\\varphi_{M}")
    assert any(s["pattern"] in ("map_signature", "write_for") for s in phi)


def test_local_scope_in_theorem_and_proof(sample_doc, sample_glossary):
    thm = next(b for b in sample_doc.blocks if b.label == "thm:stab")
    proof = next(b for b in sample_doc.blocks if b.kind == "proof")
    m = [s for s in sym(sample_glossary, "M") if s["defined_at"] == thm.id]
    assert m and m[0]["scope"]["kind"] == "blocks" and proof.id in m[0]["scope"]["blocks"]
    j = [s for s in sym(sample_glossary, "j") if s["defined_at"] == proof.id]
    assert j and j[0]["scope"]["blocks"] == [proof.id]


def test_tuple_symbol(sample_glossary):
    p = sym(sample_glossary, "P")
    assert any("poset" in s["meaning"] for s in p)


def test_index_counts_occurrences(sample_glossary):
    assert len(sample_glossary.index["\\Delta"]) >= 3
