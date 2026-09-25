"""The local web server."""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import settings
from .ingest.preamble import expand_macros_for_mathjax
from .library.store import Library, Paper
from .render.page import block_payload, toc_payload, units_payload
from .resolve.resolver import Resolver

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="PapAssist", version=__version__)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

library = Library()
_resolvers: dict[str, Resolver] = {}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _llm_client():
    """The LLM layer is optional; return None when it is not configured."""
    if not (settings.llm_enabled and settings.api_key_present):
        return None
    try:
        from .llm.client import LLMClient

        return LLMClient(model=settings.model)
    except Exception:
        return None


def get_paper(pid: str) -> Paper:
    p = library.get(pid)
    if p is None:
        raise HTTPException(404, f"unknown paper {pid}")
    return p


def get_resolver(pid: str) -> Resolver:
    with _lock:
        r = _resolvers.get(pid)
        if r is None:
            paper = get_paper(pid)
            r = Resolver(paper.doc, paper.glossary, llm=_llm_client())
            _resolvers[pid] = r
        return r


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response

    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#2a6bd6"/>'
           '<text x="16" y="22" font-size="18" font-family="Georgia,serif" font-style="italic" text-anchor="middle" fill="#fff">π</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "settings": settings.to_dict()}


@app.get("/api/settings")
def get_settings() -> dict:
    return settings.to_dict()


class SettingsIn(BaseModel):
    llm_enabled: Optional[bool] = None
    model: Optional[str] = None
    auto_enrich: Optional[bool] = None


@app.post("/api/settings")
def set_settings(body: SettingsIn) -> dict:
    if body.llm_enabled is not None:
        settings.llm_enabled = body.llm_enabled
    if body.model:
        settings.model = body.model
    if body.auto_enrich is not None:
        settings.auto_enrich = body.auto_enrich
    with _lock:
        for r in _resolvers.values():
            r.llm = _llm_client()
    return settings.to_dict()


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

@app.get("/api/library")
def list_library() -> dict:
    return {"papers": library.list()}


@app.delete("/api/papers/{pid}")
def delete_paper(pid: str) -> dict:
    with _lock:
        _resolvers.pop(pid, None)
    return {"deleted": library.delete(pid)}


class OpenIn(BaseModel):
    path: str
    main: Optional[str] = None


def _after_add(paper: Paper) -> dict:
    if settings.auto_enrich and settings.llm_enabled and settings.api_key_present:
        start_enrichment(paper.id)
    return {"paper_id": paper.id, "meta": paper.meta}


@app.post("/api/papers/open")
def open_local(body: OpenIn) -> dict:
    p = Path(body.path).expanduser()
    if not p.exists():
        raise HTTPException(400, f"path not found: {p}")
    try:
        paper = library.add([p], main_name=body.main)
    except Exception as e:  # surface the cause to the UI
        raise HTTPException(400, f"could not ingest: {e}") from e
    return _after_add(paper)


@app.post("/api/papers/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="papassist-upload-"))
    try:
        paths = []
        for f in files:
            name = Path(f.filename or "upload").name
            dest = tmp / name
            dest.write_bytes(await f.read())
            paths.append(dest)
        try:
            paper = library.add(paths)
        except Exception as e:
            raise HTTPException(400, f"could not ingest: {e}") from e
        return _after_add(paper)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# paper content
# ---------------------------------------------------------------------------

@app.get("/api/papers/{pid}")
def paper_payload(pid: str) -> dict:
    paper = get_paper(pid)
    doc = paper.doc
    glossary = paper.glossary
    from .ingest.preamble import Macro, Preamble

    pre = Preamble()
    for name, m in doc.macros.items():
        pre.macros[name] = Macro(name, m.get("nargs", 0), m.get("body", ""), m.get("default"), m.get("kind", "newcommand"))
    return {
        "id": pid,
        "meta": paper.meta,
        "title": doc.title,
        "authors": doc.authors,
        "blocks": block_payload(doc, glossary),
        "units": units_payload(doc),
        "toc": toc_payload(doc),
        "macros": expand_macros_for_mathjax(pre),
        "terms": [{"term": t["term"], "display": t.get("display"), "status": t.get("status", "defined"), "block": t.get("definition_block")} for t in glossary.terms],
        "symbol_count": len(glossary.symbols),
        "llm": paper.meta.get("llm", {"status": "not_run"}),
        "settings": settings.to_dict(),
        "warnings": doc.warnings[:20],
    }


@app.get("/api/papers/{pid}/status")
def paper_status(pid: str) -> dict:
    paper = get_paper(pid)
    job = _jobs.get(pid)
    return {"llm": paper.meta.get("llm", {"status": "not_run"}), "job": job, "settings": settings.to_dict()}


@app.get("/api/papers/{pid}/resolve")
def resolve(
    pid: str,
    uid: Optional[str] = None,
    key: Optional[str] = None,
    tex: Optional[str] = None,
    base: Optional[str] = None,
    term: Optional[str] = None,
    mid: Optional[str] = None,
    op: Optional[str] = None,
    label: Optional[str] = None,
    cite: Optional[str] = None,
    block: Optional[str] = Query(default=None),
) -> dict:
    r = get_resolver(pid)
    if uid is not None and uid in r._unit_lookup:
        card = r.resolve_unit(uid, block)
    elif key is not None:
        card = r.resolve_by_key(key, tex or "", base, block)
    elif uid is not None:
        card = {"kind": "symbol", "status": "not_found", "tex": tex or "", "key": key or "", "entries": [], "units": {}}
    elif term is not None:
        card = r.resolve_term(term, block)
    elif mid is not None:
        card = r.resolve_formula(mid, block)
    elif op is not None:
        card = r.resolve_operator(op)
    elif label is not None:
        card = r.label_card(label)
    elif cite is not None:
        card = r.cite_card([k for k in cite.split(",") if k])
    else:
        raise HTTPException(400, "nothing to resolve")
    card["block"] = block
    return card


@app.get("/api/papers/{pid}/block/{bid}")
def block(pid: str, bid: str) -> dict:
    return get_resolver(pid).block_card(bid)


@app.get("/api/papers/{pid}/glossary")
def glossary_dump(pid: str) -> dict:
    g = get_paper(pid).glossary
    return {"symbols": g.symbols, "terms": g.terms, "llm": g.llm}


@app.get("/api/papers/{pid}/source/{path:path}")
def source_file(pid: str, path: str):
    paper = get_paper(pid)
    p = (paper.folder / "source" / path).resolve()
    if not str(p).startswith(str((paper.folder / "source").resolve())) or not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p))


# ---------------------------------------------------------------------------
# LLM endpoints
# ---------------------------------------------------------------------------

class ExplainIn(BaseModel):
    kind: str                   # symbol | term
    key: str                    # canonical key or term
    tex: Optional[str] = None
    block: Optional[str] = None


@app.post("/api/papers/{pid}/explain")
def explain(pid: str, body: ExplainIn) -> dict:
    """Ask the LLM what the paper's own text says about a symbol or term."""
    r = get_resolver(pid)
    if r.llm is None:
        raise HTTPException(409, "LLM is not configured (set ANTHROPIC_API_KEY and enable it in settings)")
    from .llm.explain import explain_in_context

    paper = get_paper(pid)
    try:
        return explain_in_context(paper, r, body.kind, body.key, body.tex or "", body.block)
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e


class ParaphraseIn(BaseModel):
    text: str
    context: Optional[str] = None


@app.post("/api/papers/{pid}/paraphrase")
def paraphrase(pid: str, body: ParaphraseIn) -> dict:
    r = get_resolver(pid)
    if r.llm is None:
        raise HTTPException(409, "LLM is not configured")
    from .llm.explain import paraphrase_plain

    paper = get_paper(pid)
    try:
        return paraphrase_plain(paper, r, body.text, body.context or "")
    except Exception as e:
        raise HTTPException(502, f"LLM call failed: {e}") from e


@app.post("/api/papers/{pid}/enrich")
def enrich(pid: str) -> dict:
    get_paper(pid)
    if _llm_client() is None:
        raise HTTPException(409, "LLM is not configured")
    start_enrichment(pid)
    return {"started": True}


def start_enrichment(pid: str) -> None:
    with _lock:
        if pid in _jobs and _jobs[pid].get("status") == "running":
            return
        _jobs[pid] = {"status": "running", "message": "LLM glossary pass running…"}

    def run() -> None:
        try:
            paper = get_paper(pid)
            paper.save_meta(llm={"status": "running"})
            from .llm.enrich import enrich_glossary

            client = _llm_client()
            if client is None:
                raise RuntimeError("LLM not configured")
            g = enrich_glossary(paper, client)
            paper.save_glossary(g)
            paper.save_meta(llm=g.llm, symbols=len(g.symbols), terms=len(g.terms))
            with _lock:
                if pid in _resolvers:
                    _resolvers[pid].refresh(g)
                _jobs[pid] = {"status": "done", "message": "LLM glossary pass finished"}
        except Exception as e:
            traceback.print_exc()
            with _lock:
                _jobs[pid] = {"status": "error", "message": str(e)[:500]}
            try:
                get_paper(pid).save_meta(llm={"status": "error", "error": str(e)[:500]})
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
