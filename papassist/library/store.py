"""Where papers live on disk.

``library/<paper_id>/``
    ``source/``        the LaTeX sources (PapAssist's own copy; drawings replaced by placeholders)
    ``meta.json``      title, authors, main file, timestamps, status
    ``doc.json``       the document model
    ``glossary.json``  symbols, terms, occurrence index
    ``cache/``         LLM answers keyed by a hash of their inputs
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Optional

from ..glossary.build import Glossary, build_glossary
from ..ingest.document import Document
from ..ingest.pipeline import ingest_folder
from ..ingest.pandoc_runner import find_main_tex


def default_library_dir() -> Path:
    env = os.environ.get("PAPASSIST_LIBRARY")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "library"


def slugify(s: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:n] or "paper"


class Paper:
    def __init__(self, folder: Path):
        self.folder = folder
        self.id = folder.name
        self._doc: Optional[Document] = None
        self._glossary: Optional[Glossary] = None

    @property
    def meta(self) -> dict:
        p = self.folder / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def save_meta(self, **updates) -> dict:
        m = self.meta
        m.update(updates)
        (self.folder / "meta.json").write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
        return m

    @property
    def doc(self) -> Document:
        if self._doc is None:
            self._doc = Document.from_dict(json.loads((self.folder / "doc.json").read_text(encoding="utf-8")))
        return self._doc

    @property
    def glossary(self) -> Glossary:
        if self._glossary is None:
            p = self.folder / "glossary.json"
            self._glossary = Glossary.from_dict(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else Glossary()
        return self._glossary

    def save_glossary(self, g: Glossary) -> None:
        self._glossary = g
        (self.folder / "glossary.json").write_text(json.dumps(g.to_dict(), ensure_ascii=False), encoding="utf-8")

    def cache_get(self, key: str) -> Optional[dict]:
        p = self.folder / "cache" / (key + ".json")
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def cache_put(self, key: str, value: dict) -> None:
        d = self.folder / "cache"
        d.mkdir(exist_ok=True)
        (d / (key + ".json")).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class Library:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or default_library_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._papers: dict[str, Paper] = {}

    # -- listing -----------------------------------------------------------------
    def list(self) -> list[dict]:
        out = []
        for folder in sorted(self.root.iterdir()):
            if not (folder / "meta.json").exists():
                continue
            m = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            m["id"] = folder.name
            out.append(m)
        out.sort(key=lambda m: m.get("added", 0), reverse=True)
        return out

    def get(self, pid: str) -> Optional[Paper]:
        if pid in self._papers:
            return self._papers[pid]
        folder = self.root / pid
        if not (folder / "doc.json").exists():
            return None
        p = Paper(folder)
        self._papers[pid] = p
        return p

    def delete(self, pid: str) -> bool:
        folder = self.root / pid
        if not folder.exists() or not (folder / "meta.json").exists():
            return False
        shutil.rmtree(folder)
        self._papers.pop(pid, None)
        return True

    # -- adding ------------------------------------------------------------------
    def _stage_sources(self, staging: Path, inputs: list[Path]) -> None:
        """Copy files/folders/archives into ``staging``."""
        staging.mkdir(parents=True, exist_ok=True)
        for src in inputs:
            if src.is_dir():
                for item in src.iterdir():
                    if item.name.startswith("."):
                        continue
                    dest = staging / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
            elif src.suffix.lower() == ".zip":
                with zipfile.ZipFile(src) as z:
                    for info in z.infolist():
                        name = info.filename
                        if name.startswith("/") or ".." in Path(name).parts:
                            continue
                        z.extract(info, staging)
            elif src.name.lower().endswith((".tar.gz", ".tgz", ".tar")):
                with tarfile.open(src) as t:
                    members = [m for m in t.getmembers() if not (m.name.startswith("/") or ".." in Path(m.name).parts)]
                    t.extractall(staging, members=members)
            elif src.suffix.lower() == ".gz":
                # arXiv sometimes serves a single gzipped .tex
                import gzip

                data = gzip.open(src).read()
                (staging / (src.stem if src.stem.endswith(".tex") else src.stem + ".tex")).write_bytes(data)
            else:
                shutil.copy2(src, staging / src.name)
        # flatten a single top-level directory (typical for archives)
        entries = [e for e in staging.iterdir() if not e.name.startswith(".")]
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for item in list(inner.iterdir()):
                shutil.move(str(item), str(staging / item.name))
            inner.rmdir()

    def add(self, inputs: list[Path], main_name: Optional[str] = None) -> Paper:
        """Ingest sources (files, folders, archives) and return the Paper."""
        tmp = self.root / f".staging-{int(time.time() * 1000)}"
        try:
            self._stage_sources(tmp / "source", [Path(p) for p in inputs])
            source = tmp / "source"
            main = (source / main_name) if main_name else find_main_tex(source)
            if main is None or not main.exists():
                raise ValueError("No LaTeX file with \\documentclass was found in the input.")
            digest = hashlib.sha1()
            for p in sorted(source.rglob("*.tex")):
                digest.update(p.read_bytes())
            pid = "p" + digest.hexdigest()[:10]
            doc = ingest_folder(source, pid, main=main)
            glossary = build_glossary(doc)
            folder = self.root / pid
            if folder.exists():
                shutil.rmtree(folder)
            shutil.move(str(tmp), str(folder))
            paper = Paper(folder)
            (folder / "doc.json").write_text(json.dumps(doc.to_dict(), ensure_ascii=False), encoding="utf-8")
            paper.save_glossary(glossary)
            paper.save_meta(
                title=doc.title or main.stem, authors=doc.authors, main=str(main.relative_to(source)), added=time.time(),
                blocks=len(doc.blocks), formulas=len(doc.math), symbols=len(glossary.symbols), terms=len(glossary.terms),
                llm={"status": "not_run"},
            )
            self._papers[pid] = paper
            return paper
        finally:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
