"""Version history for every document and garden note.

Not every keystroke: a version is the state at the end of a burst of changes by
one author ("you" typing in the app, or "agent" -- any change made outside the
app's editor, which is almost always the writing chat). A new version starts when
the author changes or after ten quiet-ish minutes, and always before a restore or
an accepted suggestion, so any earlier state can be got back.

Kept under ``DATA_DIR/versions/<key>/``: ``<n>.md`` files (the whole file, front
block included) and ``index.json`` listing them. Nothing is ever pruned.
"""

import hashlib
import json
import threading
import time
from pathlib import Path

BURST_SECONDS = 10 * 60


class Versions:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def _dir(self, side: str, item_id: str) -> Path:
        return self.root / hashlib.sha1(f"{side}:{item_id}".encode()).hexdigest()[:16]

    def move(self, side: str, old_id: str, new_id: str) -> None:
        """An item was renamed: its history goes with it (unless the new name already has one)."""
        with self._lock:
            old, new = self._dir(side, old_id), self._dir(side, new_id)
            if old.is_dir() and not new.exists():
                old.rename(new)

    def _index(self, folder: Path) -> list[dict]:
        index = folder / "index.json"
        return (
            json.loads(index.read_text()).get("versions", []) if index.exists() else []
        )

    def _save_index(
        self, folder: Path, side: str, item_id: str, versions: list[dict]
    ) -> None:
        (folder / "index.json").write_text(
            json.dumps(
                {"side": side, "id": item_id, "versions": versions},
                indent=1,
                ensure_ascii=False,
            )
        )

    def record(
        self, side: str, item_id: str, text: str, who: str, fresh: bool = False
    ) -> None:
        """Note this content. Extends the latest version when it's the same author's ongoing burst."""
        with self._lock:
            folder = self._dir(side, item_id)
            folder.mkdir(parents=True, exist_ok=True)
            versions = self._index(folder)
            now = time.time()
            if versions:
                last = versions[-1]
                if (folder / last["file"]).read_text(errors="replace") == text:
                    return
                if (
                    not fresh
                    and not last.get("sealed")
                    and last["who"] == who
                    and now - last["started"] < BURST_SECONDS
                ):
                    (folder / last["file"]).write_text(text)
                    last["at"] = now
                    self._save_index(folder, side, item_id, versions)
                    return
            n = (int(versions[-1]["file"].split(".")[0]) + 1) if versions else 1
            name = f"{n}.md"
            (folder / name).write_text(text)
            versions.append({"file": name, "who": who, "started": now, "at": now})
            self._save_index(folder, side, item_id, versions)

    def seal(self, side: str, item_id: str) -> None:
        """Close the current burst: the next change starts a new version."""
        with self._lock:
            folder = self._dir(side, item_id)
            versions = self._index(folder)
            if versions and not versions[-1].get("sealed"):
                versions[-1]["sealed"] = True
                self._save_index(folder, side, item_id, versions)

    def has_any(self, side: str, item_id: str) -> bool:
        return bool(self._index(self._dir(side, item_id)))

    def list(self, side: str, item_id: str) -> list[dict]:
        folder = self._dir(side, item_id)
        out = []
        for v in self._index(folder):
            text = (folder / v["file"]).read_text(errors="replace")
            out.append(
                {
                    "vid": v["file"].split(".")[0],
                    "who": v["who"],
                    "at": v["at"],
                    "words": len(text.split()),
                }
            )
        return list(reversed(out))

    def text(self, side: str, item_id: str, vid: str) -> str:
        if not vid.isdigit():
            raise KeyError(vid)
        path = self._dir(side, item_id) / f"{vid}.md"
        if not path.exists():
            raise KeyError(vid)
        return path.read_text(errors="replace")
