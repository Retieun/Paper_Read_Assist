from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper"


def test_open_and_read_paper(client):
    r = client.post("/api/papers/open", json={"path": str(FIXTURE)})
    assert r.status_code == 200, r.text
    pid = r.json()["paper_id"]
    lib = client.get("/api/library").json()["papers"]
    assert lib[0]["id"] == pid and lib[0]["title"] == "A Sample Paper on Persistence"
    payload = client.get(f"/api/papers/{pid}").json()
    assert len(payload["blocks"]) > 10 and payload["units"] and payload["toc"]
    assert any('class="pa-term' in b["html"] for b in payload["blocks"])
    assert "kk" in payload["macros"]
    # a unit, a term, an operator, a label, a citation
    uid = next(k for k, v in payload["units"].items() if v[0] == "k")
    assert client.get(f"/api/papers/{pid}/resolve", params={"uid": uid}).json()["entries"][0]["meaning_text"] == "field"
    assert client.get(f"/api/papers/{pid}/resolve", params={"term": "vertex prime"}).json()["status"] == "found"
    assert client.get(f"/api/papers/{pid}/resolve", params={"op": "⊕"}).json()["name"] == "direct sum"
    assert client.get(f"/api/papers/{pid}/resolve", params={"label": "def:pm"}).json()["status"] == "found"
    assert client.get(f"/api/papers/{pid}/resolve", params={"cite": "chazal2016structure"}).json()["entries"][0]["arxiv"] == "1207.3674"
    assert client.get(f"/api/papers/{pid}/resolve", params={"key": "k", "tex": "k"}).json()["status"] == "found"
    g = client.get(f"/api/papers/{pid}/glossary").json()
    assert g["terms"] and g["symbols"]
    # LLM endpoints are refused cleanly when not configured
    assert client.post(f"/api/papers/{pid}/explain", json={"kind": "symbol", "key": "k"}).status_code == 409
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/").status_code == 200
    assert client.delete(f"/api/papers/{pid}").json()["deleted"] is True


def test_open_missing_path(client):
    assert client.post("/api/papers/open", json={"path": "/nonexistent/paper.tex"}).status_code == 400
