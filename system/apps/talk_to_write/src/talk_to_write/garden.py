"""The Garden tab: the user's notes folder (an Obsidian-style vault), read and edited in place.

The folder is a setting with no default (see ``notes_folder``): until the user connects
one, the tab says so. A note whose properties say ``publish: true`` is shown as public
(something else, such as a site publisher, may publish those). Nothing here copies the
vault -- every read and write goes straight to the note's own file. Deleting moves a
note into the vault's ``.trash`` folder (Obsidian's own trash), so it can be got back
until the trash is emptied.
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".obsidian", ".trash", ".git"}
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
PUBLISH = re.compile(r"^publish:\s*(true|yes)\s*$", re.M | re.I)
# [[Target]], [[Target|words]], [[Target#Heading]], [[Target#Heading|words]], and ![[...]] embeds
WIKILINK = re.compile(r"(!?)\[\[([^\]|#]*)(?:#([^\]|]*))?(?:\|([^\]]*))?\]\]")
FENCE = re.compile(r"^(```|~~~)")
# Characters Obsidian won't allow in a note's name.
FORBIDDEN_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')
# The change watcher rescans every couple of seconds; this is the fallback when it hasn't.
LISTING_SECONDS = 10.0


class GardenError(Exception):
    """Something the user should be told about in plain words."""


@dataclass(frozen=True)
class GardenNote:
    id: str  # path inside the vault without ".md"
    title: str
    public: bool
    modified: float


def notes_folder(data_dir: Path) -> Path | None:
    """The connected notes folder, or None when the user hasn't connected one.

    ``TALK_TO_WRITE_NOTES_FOLDER`` wins; otherwise ``notes_folder`` in the app's
    ``settings.json`` (under its data folder). There is deliberately no default.
    """
    value = os.environ.get("TALK_TO_WRITE_NOTES_FOLDER", "").strip()
    if not value:
        try:
            settings = json.loads((data_dir / "settings.json").read_text())
            value = str(settings.get("notes_folder") or "").strip()
        except (OSError, ValueError, AttributeError):
            value = ""
    return Path(os.path.expanduser(value)) if value else None


def is_public(text: str) -> bool:
    match = FRONTMATTER.match(text)
    return bool(match and PUBLISH.search(match.group(1)))


def outside_code(body: str, change) -> str:
    """Apply `change` to the text of a note outside its fenced code blocks."""
    out, buf, in_fence = [], [], False
    for line in body.splitlines(keepends=True):
        if FENCE.match(line.lstrip()):
            chunk = "".join(buf)
            out.append(chunk if in_fence else change(chunk))
            buf, in_fence = [], not in_fence
            out.append(line)
            continue
        buf.append(line)
    chunk = "".join(buf)
    out.append(chunk if in_fence else change(chunk))
    return "".join(out)


def split_frontmatter(text: str) -> tuple[str, str]:
    """(the raw property block including its fences, body)."""
    match = FRONTMATTER.match(text)
    if not match:
        return "", text
    return text[: match.end()], text[match.end() :]


class Garden:
    def __init__(self, root: Path | None) -> None:
        self.connected = root is not None
        # An unconnected garden points at a folder that never exists, so it is never available.
        self.root = root if root is not None else Path("/nonexistent-notes-folder")
        self._listing: tuple[float, list[GardenNote]] | None = None
        self._lock = threading.Lock()
        # note id -> (mtime, public?), so a rescan reads only the notes that changed
        self._public: dict[str, tuple[float, bool]] = {}
        # note id -> (mtime, lowercased text), for searching without rereading every file
        self._text: dict[str, tuple[float, str]] = {}

    def available(self) -> bool:
        return self.connected and self.root.is_dir()

    def path(self, note_id: str) -> Path:
        path = (self.root / f"{note_id}.md").resolve()
        if self.root.resolve() not in path.parents:
            raise GardenError("That note isn't in your garden folder.")
        return path

    def scan(self) -> dict[str, float]:
        """Every note's id and when it was last changed (no file contents read)."""
        stats: dict[str, float] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ]
            for name in filenames:
                if name.endswith(".md"):
                    full = Path(dirpath) / name
                    try:
                        stats[str(full.relative_to(self.root).with_suffix(""))] = (
                            full.stat().st_mtime
                        )
                    except OSError:
                        continue
        return stats

    def refresh(self, stats: dict[str, float]) -> None:
        """Rebuild the list from a scan, reading a note's properties only when it changed."""
        notes: list[GardenNote] = []
        for note_id, mtime in stats.items():
            cached = self._public.get(note_id)
            if cached is None or cached[0] != mtime:
                try:
                    with self.path(note_id).open(errors="replace") as handle:
                        cached = (mtime, is_public(handle.read(1500)))
                except (OSError, GardenError):
                    continue
                self._public[note_id] = cached
            notes.append(GardenNote(note_id, Path(note_id).name, cached[1], mtime))
        notes.sort(key=lambda n: n.modified, reverse=True)
        with self._lock:
            self._listing = (time.monotonic(), notes)

    def notes(self) -> list[GardenNote]:
        """Every note, most recently edited first. The change watcher keeps this fresh."""
        listing = self._listing
        if listing is None or time.monotonic() - listing[0] > LISTING_SECONDS:
            self.refresh(self.scan())
            listing = self._listing
        return listing[1] if listing else []

    def search(self, query: str) -> list[GardenNote]:
        """Notes whose title has every word first, then notes whose text has every word."""
        terms = query.lower().split()
        notes = self.notes()
        titled = [n for n in notes if all(t in n.title.lower() for t in terms)]
        chosen = set(titled)
        rest = []
        for n in notes:
            if n in chosen:
                continue
            cached = self._text.get(n.id)
            if cached is None or cached[0] != n.modified:
                try:
                    cached = (
                        n.modified,
                        self.path(n.id).read_text(errors="replace").lower(),
                    )
                except OSError:
                    continue
                self._text[n.id] = cached
            if all(t in cached[1] for t in terms):
                rest.append(n)
        return titled + rest

    def forget_listing(self) -> None:
        with self._lock:
            self._listing = None

    def read(self, note_id: str) -> str:
        path = self.path(note_id)
        if not path.exists():
            raise GardenError(
                "That note doesn't exist any more. It may have been renamed or deleted in Obsidian."
            )
        return path.read_text(errors="replace")

    def write(self, note_id: str, text: str) -> float:
        path = self.path(note_id)
        if not path.exists():
            raise GardenError(
                "That note doesn't exist any more. It may have been renamed or deleted in Obsidian."
            )
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
        self.forget_listing()
        return path.stat().st_mtime

    def rename(self, note_id: str, title: str) -> str:
        """Give a note a new title (its file name), and point every [[link]] to it at the new
        title, as Obsidian does. Returns the note's new id."""
        title = title.strip()
        if not title:
            raise GardenError("Give the note a title first.")
        if FORBIDDEN_NAME.search(title):
            raise GardenError(
                'A note title can\'t contain any of these: \\ / : * ? " < > | # ^ [ ]'
            )
        old = self.path(note_id)
        if not old.exists():
            raise GardenError(
                "That note doesn't exist any more. It may have been renamed or deleted in Obsidian."
            )
        parent = Path(note_id).parent
        new_id = str(parent / title) if str(parent) != "." else title
        if new_id == note_id:
            return note_id
        new = self.path(new_id)
        if new.exists() and new.name.lower() != old.name.lower():
            raise GardenError(f'There\'s already a note called "{title}".')
        old_name = old.stem.lower()

        def relink(m: re.Match) -> str:
            target = m.group(2)
            if Path(target.strip()).name.lower() != old_name:
                return m.group(0)
            head = target.strip().rpartition("/")[0]
            return (
                f"{m.group(1)}[[{head + '/' if head else ''}{title}"
                + (f"#{m.group(3)}" if m.group(3) is not None else "")
                + (f"|{m.group(4)}" if m.group(4) is not None else "")
                + "]]"
            )

        def relinked(text: str) -> str:
            return outside_code(text, lambda chunk: WIKILINK.sub(relink, chunk))

        # Links in the note to itself move with it; then the file takes its new name.
        text = old.read_text(errors="replace")
        updated = relinked(text)
        if updated != text:
            self.write(note_id, updated)
        os.replace(old, new)
        for other in self.scan():
            if other == new_id:
                continue
            path = self.path(other)
            try:
                current = path.read_text(errors="replace")
            except OSError:
                continue
            if "[[" not in current:
                continue
            updated = relinked(current)
            if updated != current:
                self.write(other, updated)
        self.forget_listing()
        return new_id

    # -- trash (Obsidian's own .trash folder) -----------------------------------

    @property
    def trash_dir(self) -> Path:
        return self.root / ".trash"

    def delete(self, note_id: str) -> str:
        path = self.path(note_id)
        if not path.exists():
            raise GardenError("That note doesn't exist any more.")
        self.trash_dir.mkdir(exist_ok=True)
        target = self.trash_dir / path.name
        n = 2
        while target.exists():
            target = self.trash_dir / f"{path.stem} {n}{path.suffix}"
            n += 1
        os.replace(path, target)
        self.forget_listing()
        return target.name

    def trash(self) -> list[dict]:
        if not self.trash_dir.is_dir():
            return []
        items = []
        for path in self.trash_dir.glob("*.md"):
            stat = path.stat()
            # Moving a file changes its ctime, not its mtime: ctime is when it was deleted.
            items.append(
                {"id": path.name, "title": path.stem, "deleted": stat.st_ctime}
            )
        items.sort(key=lambda i: i["deleted"], reverse=True)
        return items

    def _trashed(self, name: str) -> Path:
        path = (self.trash_dir / name).resolve()
        if path.parent != self.trash_dir.resolve() or not path.exists():
            raise GardenError("That note isn't in the Trash any more.")
        return path

    def restore(self, name: str) -> str:
        path = self._trashed(name)
        target = self.root / path.name
        n = 2
        while target.exists():
            target = self.root / f"{path.stem} {n}{path.suffix}"
            n += 1
        os.replace(path, target)
        self.forget_listing()
        return target.stem

    def purge(self, name: str) -> None:
        self._trashed(name).unlink()

    def empty_trash(self) -> None:
        for item in self.trash():
            self.purge(item["id"])
