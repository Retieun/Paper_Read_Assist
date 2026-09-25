import json
import shutil
from pathlib import Path

from papassist.library.store import Library
from papassist.llm.client import FakeLLMClient
from papassist.llm.enrich import enrich_glossary
from papassist.llm.explain import explain_in_context, paraphrase_plain
from papassist.resolve.resolver import Resolver

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper"


def test_enrichment_merges_and_caches(tmp_path):
    lib = Library(tmp_path / "lib")
    paper = lib.add([FIXTURE])
    doc = paper.doc
    thm = next(b for b in doc.blocks if b.label == "thm:stab")
    fake = FakeLLMClient(json_answers=[{
        "symbols": [{"tex": "\\varepsilon", "meaning": "the interleaving parameter", "category": "number", "scope": "local",
                     "scope_blocks": [thm.id], "defined_at": thm.id, "evidence": "$\\varepsilon$-interleaved"}],
        "terms": [{"term": "local cohomology", "defined": False, "definition_block": "", "definition": "", "cite_keys": [], "depends_on": []}],
    }])
    g = enrich_glossary(paper, fake)
    assert g.llm["status"] == "done" and g.llm["symbols_added"] == 1 and g.llm["terms_added"] == 1
    sys_blocks = fake.calls[0]["system"]
    assert sys_blocks[1]["cache_control"]["type"] == "ephemeral" and "[" + thm.id in sys_blocks[1]["text"]
    paper.save_glossary(g)
    r = Resolver(doc, g)
    uid = next(str(u["id"]) for m in doc.math.values() if m.block == thm.id for u in m.units if u["key"] == "\\varepsilon")
    card = r.resolve_unit(uid, thm.id)
    assert card["entries"][0]["source"] == "llm" and card["entries"][0]["meaning_text"] == "the interleaving parameter"
    assert r.resolve_term("local cohomology", None)["status"] == "undefined"
    # second run uses the disk cache: no new call
    g2 = enrich_glossary(paper, fake)
    assert len(fake.calls) == 1 and g2.llm["symbols_added"] == 1


def test_explain_and_paraphrase_are_grounded(tmp_path):
    lib = Library(tmp_path / "lib")
    paper = lib.add([FIXTURE])
    fake = FakeLLMClient(json_answers=[{"found": False, "meaning": "", "evidence_blocks": [], "evidence_quote": "", "note": "used without definition"}],
                         text_answers=["Plainly: a functor from a poset."])
    r = Resolver(paper.doc, paper.glossary, llm=fake)
    e = explain_in_context(paper, r, "symbol", "Q", "Q", None)
    assert e["found"] is False and e["note"] == "used without definition" and e["meaning_html"] == ""
    p = paraphrase_plain(paper, r, "A persistence module is a functor $M\\colon P\\to\\mathbf{Vec}$.", "persistence module")
    assert "functor" in p["text"] and p["units"] == {} or "pa-math" in p["html"]
