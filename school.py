"""Lab capture and PDF notes.

Lab capture: on the iPad you name the experiment, set the columns (with
units), type readings row by row and take photos of the setup. Saving writes
a folder in lab/ (next to this file, or "lab_folder" in settings.json):
data.csv (opens in Excel), notes.txt and the photos.

PDF notes: the PDFs in your notes folder ("notes_folder", default your
Documents folder and its subfolders) can be opened on the iPad and written
on with the Pencil. Saving writes "<name> (notes).pdf" next to the original;
the original is never changed. Files are only ever served by an id."""
import csv
import hashlib
import os
import re
import time

import hostsettings

HERE = os.path.dirname(os.path.abspath(__file__))
hostsettings.register({"lab_folder": "", "notes_folder": ""})
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp")
MAX_PDFS = 400
_pdfs = {"at": 0.0, "root": None, "list": [], "by_id": {}}


def _safe(name, fallback):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip().strip(".")[:80]
    return name or fallback


# ---------------------------------------------------------------- lab
def lab_root():
    return hostsettings.get("lab_folder").strip() or os.path.join(HERE, "lab")


def lab_save(data):
    """{name, columns: [..], rows: [[..]], notes, folder (to add to an earlier save)} -> {folder, rows}."""
    if not isinstance(data, dict):
        raise ValueError("expected a lab sheet")
    cols = [str(c)[:60] for c in (data.get("columns") or [])][:30]
    rows = [[str(v)[:200] for v in r][:30] for r in (data.get("rows") or []) if isinstance(r, list)][:5000]
    rows = [r for r in rows if any(v.strip() for v in r)]
    folder = _lab_folder(data.get("folder")) or os.path.join(
        lab_root(), time.strftime("%Y-%m-%d %H-%M ") + _safe(data.get("name"), "Lab"))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "data.csv"), "w", newline="", encoding="utf-8-sig") as f:   # BOM: Excel reads units like °C
        w = csv.writer(f)
        if cols:
            w.writerow(cols)
        w.writerows(rows)
    notes = str(data.get("notes") or "").strip()
    if notes:
        with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as f:
            f.write(notes + "\n")
    return {"folder": os.path.basename(folder), "path": folder, "rows": len(rows)}


def _lab_folder(name):
    """An earlier save's folder, only by its name inside the lab folder."""
    if not name:
        return None
    name = os.path.basename(str(name))
    p = os.path.join(lab_root(), name)
    return p if name and os.path.isdir(p) else None


def lab_photo(folder, filename, body):
    p = _lab_folder(folder)
    if not p:
        raise ValueError("save the sheet first")
    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext not in IMAGE_EXT:
        ext = ".jpg"
    if len(body) > 40 << 20:
        raise ValueError("that photo is too big")
    n = len([f for f in os.listdir(p) if f.lower().startswith("photo")]) + 1
    out = os.path.join(p, f"photo {n:02d}{ext}")
    with open(out, "wb") as f:
        f.write(body)
    return {"saved": os.path.basename(out)}


def lab_list():
    root = lab_root()
    try:
        names = sorted((n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))), reverse=True)[:50]
    except OSError:
        names = []
    out = []
    for n in names:
        p = os.path.join(root, n)
        files = os.listdir(p)
        out.append({"folder": n, "photos": sum(f.lower().startswith("photo") for f in files), "csv": "data.csv" in files})
    return {"root": root, "sessions": out}


def lab_open(folder=None):
    p = _lab_folder(folder) or lab_root()
    os.makedirs(p, exist_ok=True)
    os.startfile(p)


# ---------------------------------------------------------------- PDFs
def notes_root():
    return hostsettings.get("notes_folder").strip() or os.path.join(os.path.expanduser("~"), "Documents")


def _id(path):
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")).hexdigest()[:16]


def pdfs(force=False):
    """PDFs in the notes folder (newest first), rescanned at most every 20 s."""
    root = notes_root()
    if force or root != _pdfs["root"] or time.time() - _pdfs["at"] > 20:
        found = []
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "$")) and x.lower() not in ("node_modules", "appdata")]
            for n in files:
                if n.lower().endswith(".pdf"):
                    p = os.path.join(d, n)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    found.append({"id": _id(p), "path": p, "name": n[:-4], "folder": os.path.relpath(d, root),
                                  "mtime": st.st_mtime, "size": st.st_size})
            if len(found) > MAX_PDFS * 3:
                break
        found.sort(key=lambda x: x["mtime"], reverse=True)
        found = found[:MAX_PDFS]
        _pdfs.update(at=time.time(), root=root, list=found, by_id={x["id"]: x for x in found})
    return {"root": root, "pdfs": [{k: v for k, v in x.items() if k != "path"} for x in _pdfs["list"]]}


def pdf_path(pid):
    if pid not in _pdfs["by_id"]:
        pdfs(force=True)
    x = _pdfs["by_id"].get(pid)
    return x["path"] if x and os.path.isfile(x["path"]) else None


def pdf_save(pid, body):
    """Save the written-on copy next to the original: '<name> (notes).pdf'."""
    src = pdf_path(pid)
    if not src:
        raise ValueError("that PDF is gone")
    if not body.startswith(b"%PDF"):
        raise ValueError("that isn't a PDF")
    base = src[:-4]
    if base.endswith(" (notes)"):
        out = src                                    # writing on the notes copy again: update it
    else:
        out = base + " (notes).pdf"
    tmp = out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, out)
    _pdfs["at"] = 0
    return {"saved": os.path.basename(out), "id": _id(out)}

