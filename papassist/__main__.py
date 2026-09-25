"""Start PapAssist: ``python -m papassist [paper.tex | folder | archive] [--no-browser] [--port N]``."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="papassist", description="Reading assistant for mathematics papers.")
    ap.add_argument("paper", nargs="?", help=".tex file, folder, .zip or .tar.gz of LaTeX sources to open")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="run without any LLM calls")
    args = ap.parse_args(argv)

    from .config import settings

    if args.port:
        settings.port = args.port
    if args.host:
        settings.host = args.host
    if args.no_llm:
        settings.llm_enabled = False

    from . import app as appmod

    pid = None
    if args.paper:
        p = Path(args.paper).expanduser()
        if not p.exists():
            print(f"error: {p} does not exist", file=sys.stderr)
            return 2
        print(f"Ingesting {p} ...")
        try:
            paper = appmod.library.add([p])
        except Exception as e:
            print(f"error: could not ingest: {e}", file=sys.stderr)
            return 1
        pid = paper.id
        print(f"Ready: {paper.meta.get('title')} ({paper.meta.get('blocks')} blocks, {paper.meta.get('formulas')} formulas)")
        if settings.llm_enabled and settings.api_key_present and settings.auto_enrich:
            appmod.start_enrichment(pid)

    url = f"http://{settings.host}:{settings.port}/" + (f"#paper={pid}" if pid else "")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"PapAssist running at {url}  (Ctrl+C to stop)")
    import uvicorn

    uvicorn.run(appmod.app, host=settings.host, port=settings.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
