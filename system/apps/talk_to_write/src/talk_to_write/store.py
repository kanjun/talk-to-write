"""The Canvas: documents (markdown) and pages (HTML) kept under DATA_DIR.

Layout, all under the app's data directory:

- ``canvas/<id>.md``   a document; its title is the ``title:`` property
- ``canvas/<id>.html`` a page the agent made (a dashboard, a chart...); its title is its <title>
- ``trash/``           deleted Canvas items, with ``trash/index.json`` saying when and from where
- ``media/``           images and videos added to documents, served at ``/media/<name>``
- ``transcript.jsonl`` every message spoken or typed to the app, with where the user was
- ``suggestions/``     voice-edit suggestions waiting for Accept or Skip, one file per document

The agent edits these files directly (see the talk-to-write skill); this module
is what the app's own pages use.
"""

import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
TITLE_PROP = re.compile(r"^title:\s*(.*)$", re.M)
HTML_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
KINDS = {"doc": ".md", "page": ".html"}


class StoreError(Exception):
    """Something the user should be told about in plain words."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-") or "untitled"


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def doc_title(text: str, fallback: str) -> str:
    match = FRONTMATTER.match(text)
    if match:
        title = TITLE_PROP.search(match.group(1))
        if title and unquote(title.group(1)):
            return unquote(title.group(1))
    return fallback


def page_title(text: str, fallback: str) -> str:
    found = HTML_TITLE.search(text)
    return found.group(1).strip() if found and found.group(1).strip() else fallback


def item_title(kind: str, text: str, item_id: str) -> str:
    fallback = item_id.replace("-", " ").capitalize()
    return doc_title(text, fallback) if kind == "doc" else page_title(text, fallback)


def set_title(text: str, title: str) -> str:
    line = "title: " + json.dumps(title, ensure_ascii=False)
    match = FRONTMATTER.match(text)
    if not match:
        return f"---\n{line}\n---\n\n{text}"
    block = match.group(1)
    if TITLE_PROP.search(block):
        block = TITLE_PROP.sub(lambda _m: line, block, count=1)
    else:
        block = f"{line}\n{block}" if block.strip() else line
    return f"---\n{block}\n---\n" + text[match.end() :]


def body_of(text: str) -> str:
    match = FRONTMATTER.match(text)
    return text[match.end() :] if match else text


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    @property
    def canvas(self) -> Path:
        return self.root / "canvas"

    @property
    def trash_dir(self) -> Path:
        return self.root / "trash"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def transcript_file(self) -> Path:
        return self.root / "transcript.jsonl"

    def ensure(self) -> None:
        for path in (
            self.canvas,
            self.trash_dir,
            self.media,
            self.root / "suggestions",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def path(self, kind: str, item_id: str) -> Path:
        if kind not in KINDS or not ID_OK.match(item_id):
            raise StoreError("That isn't something on the Canvas.")
        return self.canvas / f"{item_id}{KINDS[kind]}"

    # -- listing ---------------------------------------------------------------

    def items(self) -> list[dict]:
        """Everything on the Canvas, most recently edited first."""
        self.ensure()
        out = []
        for path in self.canvas.iterdir():
            kind = {".md": "doc", ".html": "page"}.get(path.suffix)
            if not kind or path.name.startswith("."):
                continue
            title = item_title(kind, path.read_text(errors="replace"), path.stem)
            out.append(
                {
                    "kind": kind,
                    "id": path.stem,
                    "title": title,
                    "modified": path.stat().st_mtime,
                }
            )
        out.sort(key=lambda i: i["modified"], reverse=True)
        return out

    # -- documents -------------------------------------------------------------

    def read(self, kind: str, item_id: str) -> str:
        path = self.path(kind, item_id)
        if not path.exists():
            raise StoreError("That isn't on the Canvas any more.")
        return path.read_text(errors="replace")

    def write(self, kind: str, item_id: str, text: str) -> float:
        path = self.path(kind, item_id)
        if not path.exists():
            raise StoreError("That isn't on the Canvas any more.")
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        return path.stat().st_mtime

    def create_doc(self, title: str = "Untitled") -> str:
        self.ensure()
        with self._lock:
            base = slugify(title)
            item_id, n = base, 2
            while any(
                (self.canvas / f"{item_id}{ext}").exists() for ext in KINDS.values()
            ):
                item_id = f"{base}-{n}"
                n += 1
            (self.canvas / f"{item_id}.md").write_text(set_title("", title) + "\n")
        return item_id

    # -- trash -----------------------------------------------------------------

    def _trash_index(self) -> dict:
        index = self.trash_dir / "index.json"
        return json.loads(index.read_text()) if index.exists() else {}

    def _save_trash_index(self, data: dict) -> None:
        (self.trash_dir / "index.json").write_text(json.dumps(data, indent=1))

    def delete(self, kind: str, item_id: str) -> str:
        self.ensure()
        path = self.path(kind, item_id)
        if not path.exists():
            raise StoreError("That isn't on the Canvas any more.")
        with self._lock:
            name = f"{int(time.time())}-{secrets.token_hex(3)}-{path.name}"
            title = item_title(kind, path.read_text(errors="replace"), item_id)
            os.replace(path, self.trash_dir / name)
            index = self._trash_index()
            index[name] = {
                "kind": kind,
                "id": item_id,
                "title": title,
                "deleted": time.time(),
            }
            self._save_trash_index(index)
        return name

    def trash(self) -> list[dict]:
        self.ensure()
        index = self._trash_index()
        items = [
            {
                "id": name,
                "title": meta["title"],
                "kind": meta["kind"],
                "deleted": meta["deleted"],
            }
            for name, meta in index.items()
            if (self.trash_dir / name).exists()
        ]
        items.sort(key=lambda i: i["deleted"], reverse=True)
        return items

    def restore(self, name: str) -> dict:
        with self._lock:
            index = self._trash_index()
            meta = index.get(name)
            source = self.trash_dir / name
            if not meta or not source.exists():
                raise StoreError("That isn't in the Trash any more.")
            item_id, n = meta["id"], 2
            while any(
                (self.canvas / f"{item_id}{ext}").exists() for ext in KINDS.values()
            ):
                item_id = f"{meta['id']}-{n}"
                n += 1
            os.replace(source, self.canvas / f"{item_id}{KINDS[meta['kind']]}")
            del index[name]
            self._save_trash_index(index)
        return {"kind": meta["kind"], "id": item_id}

    def purge(self, name: str) -> None:
        with self._lock:
            index = self._trash_index()
            if name not in index:
                raise StoreError("That isn't in the Trash any more.")
            (self.trash_dir / name).unlink(missing_ok=True)
            del index[name]
            self._save_trash_index(index)

    def empty_trash(self) -> None:
        for item in self.trash():
            self.purge(item["id"])

    # -- media -----------------------------------------------------------------

    def save_media(self, filename: str, data: bytes) -> str:
        self.ensure()
        stem = slugify(Path(filename).stem)[:40]
        ext = Path(filename).suffix.lower()
        if not re.match(r"^\.[a-z0-9]{1,5}$", ext):
            ext = ""
        name = f"{int(time.time())}-{secrets.token_hex(3)}-{stem}{ext}"
        (self.media / name).write_bytes(data)
        return name

    # -- what the user said ---------------------------------------------------------

    def record(self, text: str, context: dict, how: str) -> dict:
        self.ensure()
        entry = {
            "id": secrets.token_hex(6),
            "at": now_iso(),
            "how": how,
            "text": text,
            "context": context,
        }
        with self._lock, self.transcript_file.open("a") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def transcript(self) -> list[dict]:
        if not self.transcript_file.exists():
            return []
        out = []
        for line in self.transcript_file.read_text(errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
