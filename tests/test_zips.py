import base64
import io
import time
import zipfile
from pathlib import Path

import pytest

from papassist.library.store import Library

FIXTURE = Path(__file__).parent / "fixtures" / "sample_paper"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def fixture_files():
    return {p.relative_to(FIXTURE).as_posix(): p.read_bytes() for p in FIXTURE.rglob("*") if p.is_file()}


def with_figures(files):
    main = files["main.tex"].decode()
    main = main.replace("\\section{Preliminaries}\\label{sec:prelim}", "\\section{Preliminaries}\\label{sec:prelim}\n"
                        "\\begin{figure}[h]\\centering\\includegraphics[width=0.5\\textwidth]{figures/plot}\\caption{A plot.}\\end{figure}\n"
                        "\\begin{figure}[h]\\centering\\includegraphics{figures/diagram.pdf}\\caption{A diagram.}\\end{figure}\n", 1)
    files = dict(files)
    files["main.tex"] = main.encode()
    files["figures/plot.png"] = PNG
    files["figures/diagram.pdf"] = b"%PDF-1.4 fake"
    return files


def make_zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in entries.items():
            z.writestr(arc, data)
    return path


def add(tmp_path, name, entries):
    lib = Library(tmp_path / "lib")
    return lib, lib.add([make_zip(tmp_path / name, entries)])


def test_zip_with_figures_and_subfolder(tmp_path):
    lib, paper = add(tmp_path, "a.zip", with_figures(fixture_files()))
    assert paper.meta["title"] == "A Sample Paper on Persistence"
    html = " ".join(b.html for b in paper.doc.blocks)
    assert 'src="pa-asset/figures/plot.png"' in html and "diagram.pdf" in html   # pandoc resolves the extension
    assert (paper.folder / "source" / "figures" / "plot.png").exists()


def test_zip_with_wrapper_folder_and_macosx_junk(tmp_path):
    files = {"paper/" + k: v for k, v in with_figures(fixture_files()).items()}
    files["__MACOSX/paper/._main.tex"] = b"\x00\x05\x16\x07junk"
    lib, paper = add(tmp_path, "b.zip", files)
    assert paper.meta["title"] == "A Sample Paper on Persistence"
    assert (paper.folder / "source" / "main.tex").exists()          # wrapper folder flattened


def test_windows_style_zip_bom_crlf_backslashes(tmp_path):
    files = fixture_files()
    files["main.tex"] = ("\ufeff" + files["main.tex"].decode().replace("\n", "\r\n")).encode("utf-8")
    entries = {k.replace("/", "\\"): v for k, v in files.items()}
    lib, paper = add(tmp_path, "c.zip", entries)
    assert paper.meta["title"] == "A Sample Paper on Persistence"


def test_multi_file_project_with_bom_main_is_detected(tmp_path):
    from papassist.ingest.pandoc_runner import find_main_tex

    files = fixture_files()
    files["main.tex"] = ("\ufeff% comment first\n" + files["main.tex"].decode()).encode("utf-8")
    folder = tmp_path / "proj"
    for k, v in files.items():
        (folder / k).parent.mkdir(parents=True, exist_ok=True)
        (folder / k).write_bytes(v)
    assert find_main_tex(folder).name == "main.tex"


def test_subfiles_chapter_is_not_chosen_as_main(tmp_path):
    from papassist.ingest.pandoc_runner import find_main_tex

    folder = tmp_path / "proj"
    (folder / "chapters").mkdir(parents=True)
    for k, v in fixture_files().items():
        (folder / k).parent.mkdir(parents=True, exist_ok=True)
        (folder / k).write_bytes(v)
    (folder / "chapters" / "ch1.tex").write_text("\\documentclass[../main.tex]{subfiles}\\begin{document}Hello $x$\\end{document}")
    assert find_main_tex(folder).name == "main.tex"


def test_fragment_without_documentclass_is_wrapped(tmp_path):
    lib, paper = add(tmp_path, "g.zip", {"body.tex": b"\\section{A section}\\label{s}\nLet $k$ be a field. \\input{part}", "part.tex": b"More text with $x_i$."})
    assert paper.meta["blocks"] >= 2
    assert any("field" in b.text for b in paper.doc.blocks)


def test_macro_file_is_read_and_broken_template_is_ignored(tmp_path):
    # a macros.sty that pandoc can read: its macros are picked up
    files = fixture_files()
    files["main.tex"] = files["main.tex"].decode().replace("\\begin{document}", "\\usepackage{mymacros}\n\\begin{document}\nHere $\\Zed$.\n", 1).encode()
    files["mymacros.sty"] = b"\\ProvidesPackage{mymacros}\n\\newcommand{\\Zed}{\\mathbb{Z}}\n"
    lib, paper = add(tmp_path, "m.zip", files)
    assert not paper.doc.warnings
    assert any("mathbb{Z}" in m.tex for m in paper.doc.math.values())

    # a journal template that makes pandoc give up: it is ignored on a second attempt and the paper still opens
    files = fixture_files()
    files["main.tex"] = files["main.tex"].decode().replace("\\begin{document}", "\\usepackage{journal}\n\\begin{document}", 1).encode()
    files["journal.sty"] = b"\\ProvidesPackage{journal}\n\\def\\a{\\a\\a}\\a\n"   # infinite macro expansion
    lib, paper = add(tmp_path, "j.zip", files)
    assert paper.meta["title"] == "A Sample Paper on Persistence"
    assert paper.doc.warnings and "journal.sty" in paper.doc.warnings[0]
    src = paper.folder / "source"
    assert (src / "journal.sty").exists() and not list(src.glob("*.papassist-off"))   # put back afterwards


def test_archive_without_tex_gives_a_clear_error(tmp_path):
    lib = Library(tmp_path / "lib")
    with pytest.raises(ValueError, match="No .tex file was found.*paper.pdf"):
        lib.add([make_zip(tmp_path / "h.zip", {"paper.pdf": b"%PDF-1.4", "figs/a.png": PNG})])


def test_upload_zip_over_http_and_images_are_served(client, tmp_path):
    z = make_zip(tmp_path / "up.zip", with_figures(fixture_files()))
    with open(z, "rb") as fh:
        r = client.post("/api/papers/upload", files=[("files", ("up.zip", fh, "application/zip"))])
    assert r.status_code == 200, r.text
    pid = r.json()["paper_id"]
    payload = client.get(f"/api/papers/{pid}").json()
    html = " ".join(b["html"] for b in payload["blocks"])
    assert f'src="/api/papers/{pid}/source/figures/plot.png"' in html      # extension resolved, served from the paper folder
    assert "figures/diagram.pdf" in html and "cannot be shown inline" in html
    assert client.get(f"/api/papers/{pid}/source/figures/plot.png").status_code == 200
    assert client.get(f"/api/papers/{pid}/source/../../meta.json").status_code in (404, 400)
    client.delete(f"/api/papers/{pid}")


def _wait_job(client, job, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_chunked_upload_runs_the_ingest_in_the_background(client, tmp_path):
    data = make_zip(tmp_path / "c.zip", with_figures(fixture_files())).read_bytes()
    up = client.post("/api/uploads").json()["upload"]
    step = 7000
    for off in range(0, len(data), step):
        r = client.post(f"/api/uploads/{up}/chunk", params={"name": "sub\\c.zip", "offset": off},
                        content=data[off:off + step], headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
    last = (len(data) - 1) // step * step
    r = client.post(f"/api/uploads/{up}/chunk", params={"name": "c.zip", "offset": last}, content=data[last:])
    assert r.status_code == 200 and r.json()["received"] == len(data)          # a retried last chunk is fine
    r = client.post(f"/api/uploads/{up}/chunk", params={"name": "c.zip", "offset": 0}, content=b"x")
    assert r.status_code == 409                                                # out of order is refused
    job = client.post(f"/api/uploads/{up}/finish").json()["job"]
    j = _wait_job(client, job)
    assert j["status"] == "done", j
    pid = j["result"]["paper_id"]
    assert client.get(f"/api/papers/{pid}").json()["title"] == "A Sample Paper on Persistence"
    assert client.get(f"/api/papers/{pid}/source/figures/plot.png").status_code == 200
    assert client.post(f"/api/uploads/{up}/finish").status_code == 404        # consumed


def test_background_jobs_report_ingest_errors(client, tmp_path):
    up = client.post("/api/uploads").json()["upload"]
    assert client.post(f"/api/uploads/{up}/finish").status_code == 400        # nothing uploaded
    up = client.post("/api/uploads").json()["upload"]
    client.post(f"/api/uploads/{up}/chunk", params={"name": "paper.pdf", "offset": 0}, content=b"%PDF-1.4 fake")
    job = client.post(f"/api/uploads/{up}/finish").json()["job"]
    j = _wait_job(client, job)
    assert j["status"] == "error" and "No .tex file" in j["message"] and "paper.pdf" in j["message"]
    # the path box uses the same background path
    (tmp_path / "only.pdf").write_bytes(b"%PDF")
    job = client.post("/api/papers/open", json={"path": str(tmp_path / "only.pdf"), "background": True}).json()["job"]
    j = _wait_job(client, job)
    assert j["status"] == "error" and "could not ingest" in j["message"]
    job = client.post("/api/papers/open", json={"path": str(FIXTURE), "background": True}).json()["job"]
    j = _wait_job(client, job)
    assert j["status"] == "done" and j["message"].startswith("Saving")     # last progress message
