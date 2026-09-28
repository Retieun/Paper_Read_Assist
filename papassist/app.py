"""The local web server."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
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
_tasks: dict[str, dict] = {}
_lock = threading.Lock()
FETCH = None  # tests inject a fake network here


def _fetch():
    if FETCH is not None:
        return FETCH
    import os

    fake_dir = os.environ.get("PAPASSIST_FAKE_ARXIV")
    if fake_dir:  # testing aid: serve arXiv requests from a folder
        from .refs.arxiv import directory_fetch

        return directory_fetch(Path(fake_dir))
    from .refs.arxiv import default_fetch

    return default_fetch


def run_task(name: str, fn) -> str:
    import uuid

    job_id = uuid.uuid4().hex[:10]
    with _lock:
        _tasks[job_id] = {"id": job_id, "name": name, "status": "running", "message": "", "result": None}

    def go() -> None:
        try:
            result = fn(lambda msg: _tasks[job_id].__setitem__("message", msg))
            with _lock:
                _tasks[job_id].update({"status": "done", "result": result})
        except Exception as e:
            traceback.print_exc()
            with _lock:
                _tasks[job_id].update({"status": "error", "message": str(e)[:600]})

    threading.Thread(target=go, daemon=True).start()
    return job_id


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


IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")
FILE_EXTS = (".pdf", ".eps", ".ps", ".tif", ".tiff")
_ASSET_RE = re.compile(r'<img class="pa-img" src="pa-asset/([^"]+)" alt="([^"]*)">')


def _find_asset(source: Path, ref: str) -> Optional[Path]:
    ref = ref.replace("\\", "/").strip()
    cands = [ref] + [ref + ext for ext in IMG_EXTS + FILE_EXTS]
    for c in cands:
        p = (source / c).resolve()
        if str(p).startswith(str(source.resolve())) and p.is_file():
            return p
    # graphicspath-style: search by file name anywhere in the sources
    name = Path(ref).name
    for p in source.rglob(name + "*"):
        if p.is_file() and (p.name == name or p.stem == name):
            return p
    return None


def rewrite_assets(blocks: list[dict], paper: Paper) -> list[dict]:
    """Point figure images at the paper's own files; show a link for formats browsers cannot display."""
    source = paper.folder / "source"

    def repl(m: re.Match) -> str:
        ref, alt = m.group(1), m.group(2)
        hit = _find_asset(source, ref)
        if hit is None:
            return f'<div class="pa-figure-missing">[figure file not found: {ref}]</div>'
        rel = hit.relative_to(source).as_posix()
        url = f"/api/papers/{paper.id}/source/{rel}"
        if hit.suffix.lower() in IMG_EXTS:
            return f'<img class="pa-img" src="{url}" alt="{alt}">'
        return f'<div class="pa-figure-missing">figure file <a class="pa-extlink" href="{url}" target="_blank" rel="noopener">{rel}</a> (open it in a new tab; this format cannot be shown inline)</div>'

    for b in blocks:
        if "pa-asset/" in b["html"]:
            b["html"] = _ASSET_RE.sub(repl, b["html"])
    return blocks


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
    background: bool = False


def _ingest_job(paths: list[Path], main_name: Optional[str] = None, cleanup: Optional[Path] = None) -> str:
    """Ingest in a background thread; the HTTP request returns at once and the client polls the job."""

    def work(progress):
        try:
            paper = library.add(paths, main_name=main_name, progress=progress)
        except Exception as e:
            raise RuntimeError(f"could not ingest ({type(e).__name__}): {e}") from e
        finally:
            if cleanup is not None:
                shutil.rmtree(cleanup, ignore_errors=True)
        return _after_add(paper)

    return run_task("ingest", work)


def _after_add(paper: Paper) -> dict:
    if settings.auto_enrich and settings.llm_enabled and settings.api_key_present:
        start_enrichment(paper.id)
    return {"paper_id": paper.id, "meta": paper.meta}


@app.post("/api/papers/open")
def open_local(body: OpenIn) -> dict:
    p = Path(body.path).expanduser()
    if not p.exists():
        raise HTTPException(400, f"path not found: {p}")
    if body.background:
        return {"job": _ingest_job([p], main_name=body.main)}
    try:
        paper = library.add([p], main_name=body.main)
    except Exception as e:  # surface the cause to the UI
        traceback.print_exc()
        raise HTTPException(400, f"could not ingest ({type(e).__name__}): {e}") from e
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
            traceback.print_exc()
            raise HTTPException(400, f"could not ingest ({type(e).__name__}): {e}") from e
        return _after_add(paper)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Chunked uploads: the browser sends each file in small pieces and then asks for the
# ingest to start in the background.  Proxies in front of the server (a GitHub
# Codespace, a remote desktop) may cap the size or duration of one request; small
# requests that return immediately get through where one big, slow POST is cut off.
_uploads: dict[str, dict] = {}


@app.post("/api/uploads")
def upload_begin() -> dict:
    import uuid

    uid = uuid.uuid4().hex[:12]
    with _lock:
        _uploads[uid] = {"dir": Path(tempfile.mkdtemp(prefix="papassist-upload-")), "files": []}
    return {"upload": uid}


@app.post("/api/uploads/{uid}/chunk")
async def upload_chunk(uid: str, request: Request, name: str = Query(...), offset: int = Query(0)) -> dict:
    up = _uploads.get(uid)
    if up is None:
        raise HTTPException(404, "unknown upload (was the server restarted?); please open the file again")
    safe = Path(name.replace("\\", "/")).name or "upload"
    dest = up["dir"] / safe
    data = await request.body()
    have = dest.stat().st_size if dest.exists() else 0
    if offset == have - len(data) and len(data) > 0:
        return {"received": have}  # a retried chunk that already arrived
    if offset != have:
        raise HTTPException(409, f"chunk out of order for {safe}: have {have} bytes, got offset {offset}")
    with open(dest, "ab") as fh:
        fh.write(data)
    if safe not in up["files"]:
        up["files"].append(safe)
    return {"received": have + len(data)}


@app.post("/api/uploads/{uid}/finish")
def upload_finish(uid: str) -> dict:
    with _lock:
        up = _uploads.pop(uid, None)
    if up is None:
        raise HTTPException(404, "unknown upload (was the server restarted?); please open the file again")
    paths = [up["dir"] / n for n in up["files"] if (up["dir"] / n).exists()]
    if not paths:
        shutil.rmtree(up["dir"], ignore_errors=True)
        raise HTTPException(400, "no files were uploaded")
    return {"job": _ingest_job(paths, cleanup=up["dir"])}


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
        "blocks": rewrite_assets(block_payload(doc, glossary), paper),
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
    uid1: Optional[str] = None,
    uid2: Optional[str] = None,
    op: Optional[str] = None,
    label: Optional[str] = None,
    cite: Optional[str] = None,
    block: Optional[str] = Query(default=None),
    retag: bool = False,
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
    elif mid is not None and uid1 and uid2:
        card = r.resolve_range(mid, uid1, uid2, block)
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
    if retag:
        from .refs.lookup import retag_card

        return retag_card(card, pid)
    from .refs.service import attach_reference_info

    return attach_reference_info(card, library, get_paper(pid), get_resolver)


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
# cited papers
# ---------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = _tasks.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


@app.get("/api/papers/{pid}/references")
def references(pid: str) -> dict:
    from .refs.service import references_status

    return {"references": references_status(library, get_paper(pid))}


@app.post("/api/papers/{pid}/references/{key}/fetch")
def fetch_reference(pid: str, key: str) -> dict:
    paper = get_paper(pid)
    entry = paper.doc.bibliography.get(key)
    if entry is None:
        raise HTTPException(404, f"no bibliography entry {key}")

    def work(progress) -> dict:
        from .ingest.bib import BibEntry
        from .refs.arxiv import NoSourceError, resolve_arxiv_id

        e = BibEntry(**{k: v for k, v in entry.items() if k != "short"})
        ref_ids = paper.meta.get("ref_ids", {})
        aid = ref_ids.get(key, {}).get("arxiv")
        if not aid:
            progress("Looking the reference up on arXiv…")
            aid, how = resolve_arxiv_id(e, fetch=_fetch(), allow_search=True)
            ref_ids[key] = {"arxiv": aid, "method": how}
            paper.save_meta(ref_ids=ref_ids)
        if not aid:
            raise RuntimeError("Could not find this reference on arXiv (no identifier in the bibliography and no matching title).")
        progress(f"Downloading arXiv:{aid} and converting it…")
        try:
            other = library.add_from_arxiv(aid, fetch=_fetch(), role="reference", cited_by=pid)
        except NoSourceError as ex:
            raise RuntimeError(str(ex)) from ex
        return {"paper_id": other.id, "arxiv": aid, "title": other.meta.get("title")}

    return {"job": run_task(f"fetch {key}", work)}


@app.get("/api/papers/{pid}/lookup")
def lookup(pid: str, term: Optional[str] = None, key: Optional[str] = None, tex: Optional[str] = None, ref: Optional[str] = None) -> dict:
    from .refs.service import lookup_in_library

    paper = get_paper(pid)
    if term:
        results = lookup_in_library(library, paper, get_resolver, term=term, ref_key=ref)
    elif key:
        keys = [key]
        try:
            from .render.mathunits import analyze_math

            a = analyze_math(tex or key)
            tops = [u for u in a.units if u.parent is None]
            if tops:
                keys = list(dict.fromkeys([key, *tops[0].keys]))
        except Exception:
            pass
        results = lookup_in_library(library, paper, get_resolver, keys=keys, ref_key=ref)
    else:
        raise HTTPException(400, "term or key required")
    return {"results": results}


class ArxivIn(BaseModel):
    id: str


@app.post("/api/papers/arxiv")
def open_arxiv(body: ArxivIn) -> dict:
    from .refs.arxiv import normalize_arxiv_id

    aid = normalize_arxiv_id(body.id)
    if not aid:
        raise HTTPException(400, "not an arXiv identifier")

    def work(progress) -> dict:
        progress(f"Downloading arXiv:{aid}…")
        paper = library.add_from_arxiv(aid, fetch=_fetch(), role="paper")
        if settings.auto_enrich and settings.llm_enabled and settings.api_key_present:
            start_enrichment(paper.id)
        return {"paper_id": paper.id, "title": paper.meta.get("title")}

    return {"job": run_task(f"arxiv {aid}", work)}


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
