def uid_for(doc, key, block=None):
    for mid, m in doc.math.items():
        if block and m.block != block:
            continue
        for u in m.units:
            if u["key"] == key:
                return str(u["id"]), m.block
    raise AssertionError(f"no unit {key}")


def test_scoped_entry_wins_inside_the_proof(sample_doc, sample_resolver):
    proof = next(b for b in sample_doc.blocks if b.kind == "proof")
    uid, bid = uid_for(sample_doc, "M", proof.id)
    card = sample_resolver.resolve_unit(uid, bid)
    assert card["status"] == "found"
    assert "pfd persistence module" in card["entries"][0]["meaning_text"]
    assert card["entries"][0]["scope"]["kind"] == "blocks"


def test_paper_wide_entry_elsewhere(sample_doc, sample_resolver):
    uid, bid = uid_for(sample_doc, "k")
    card = sample_resolver.resolve_unit(uid, bid)
    assert card["entries"][0]["meaning_text"] == "field"
    assert card["macro"]["name"] == "kk"
    assert card["occurrences"]["count"] >= 2


def test_dictionary_fallback_and_wildcards(sample_doc, sample_resolver):
    uid, bid = uid_for(sample_doc, "H_{\\mathfrak{m}}^{q}")
    card = sample_resolver.resolve_unit(uid, bid)
    assert card["dictionary"] and card["dictionary"]["name"] == "local cohomology"
    assert sample_resolver.resolve_operator("⊗")["name"] == "tensor product"


def test_not_found_symbol_reports_occurrences(sample_resolver):
    card = sample_resolver.resolve_by_key("Q", "Q", "Q", None)
    assert card["status"] == "not_found" and card["entries"] == []


def test_term_card_with_dependencies(sample_resolver):
    card = sample_resolver.resolve_term("persistence modules", None)
    assert card["status"] == "found"
    assert card["entries"][0]["source"] == "paper_definition"
    assert "pa-thm-head" in card["entries"][0]["definition_html"]
    assert any(d["term"] == "pointwise finite dimensional" for d in card["depends_on"])
    assert card["entries"][0]["cite_hints"][0]["arxiv"] == "1207.3674"
    assert card["mentions"]["count"] >= 2


def test_alias_resolves(sample_resolver):
    assert sample_resolver.resolve_term("pfd", None)["status"] == "found"


def test_formula_and_label_cards(sample_doc, sample_resolver):
    mid = next(m.id for m in sample_doc.math.values() if "cases" in m.tex)
    fc = sample_resolver.resolve_formula(mid, None)
    assert fc["status"] == "found" and any(i["tex"] == "c" for i in fc["items"])
    lc = sample_resolver.label_card("thm:stab")
    assert lc["status"] == "found" and lc["heading"].startswith("Theorem 3.1")
    cc = sample_resolver.cite_card(["bauer2015induced", "nope"])
    assert cc["entries"][0]["short"] == "Bauer–Lesnick 2015" and "not found" in cc["entries"][1]["title"]
