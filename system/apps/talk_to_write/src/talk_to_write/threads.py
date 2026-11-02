"""One running history per piece of writing: everything the user has said, spoken or typed, about
a document and the documents that belong with it (an essay and its narrative map, or essays
the user has linked), so the writing chat can pick a piece back up after the user has been elsewhere.

Documents belong together when one's id extends another's (``the-quiet-hour`` and
``the-quiet-hour-narrative-map``) or when they are linked in ``related.json``
(``[["canvas/a", "k/Attention is a form of care"], ...]``). Each group's history is written,
readable, to ``threads/<first document's id>.md``; the records themselves stay in
``transcript.jsonl``.
"""

import json
import re
from pathlib import Path

MAX_HISTORY = 120_000  # characters of history handed to the chat at once (about 30k tokens); the newest are kept


def key(side: str, item_id: str) -> str:
    return f"{'k' if side in ('k', 'garden') else 'canvas'}/{item_id}"


def where(context: dict) -> list[str]:
    """The documents a message was about: the one open, and the one beside it."""
    keys = []
    for pane in (context, context.get("beside") or {}):
        item = pane.get("item") or {} if isinstance(pane, dict) else {}
        if pane.get("view") in ("doc", "page") and item.get("id"):
            keys.append(key(pane.get("side", "canvas"), item["id"]))
    return keys


def load_links(data_dir: Path) -> list[list[str]]:
    path = data_dir / "related.json"
    return json.loads(path.read_text()) if path.exists() else []


def link(data_dir: Path, keys: list[str]) -> list[list[str]]:
    """Put these documents in one group (merging any groups they were already in)."""
    groups = load_links(data_dir)
    merged = set(keys)
    rest = []
    for group in groups:
        if merged & set(group):
            merged |= set(group)
        else:
            rest.append(group)
    rest.append(sorted(merged))
    path = data_dir / "related.json"
    path.write_text(json.dumps(rest, indent=1, ensure_ascii=False))
    return rest


def extends(longer: str, shorter: str) -> bool:
    """``essay-narrative-map`` extends ``essay``; ``untitled-2`` doesn't extend ``untitled``."""
    rest = longer[len(shorter) + 1 :] if longer.startswith(shorter + "-") else ""
    return bool(rest) and not rest.isdigit()


def group_of(data_dir: Path, start: str, known: set[str]) -> list[str]:
    """Every document that belongs with ``start``, ``start`` first."""
    links = load_links(data_dir)
    group, todo = {start}, [start]
    while todo:
        current = todo.pop()
        side, _, item_id = current.partition("/")
        found = set()
        for other in known:
            o_side, _, o_id = other.partition("/")
            if o_side == side == "canvas" and (extends(o_id, item_id) or extends(item_id, o_id)):
                found.add(other)
        for linked in links:
            if current in linked:
                found |= set(linked)
        for other in found - group:
            group.add(other)
            todo.append(other)
    return [start] + sorted(group - {start})


def history(records: list[dict], group: list[str], titles: dict[str, str], limit: int = MAX_HISTORY) -> str:
    """What the user said about these documents, oldest first, as readable text."""
    members = set(group)
    parts = []
    for r in sorted(records, key=lambda r: r.get("at", "")):
        about = [k for k in where(r.get("context") or {}) if k in members]
        if not about:
            continue
        stamp = r.get("at", "")[:16].replace("T", " ")
        head = f"## {stamp} UTC, {r.get('how', 'spoken')}, on {' and '.join(titles.get(k, k) for k in about)}"
        ptr = (r.get("context") or {}).get("pointing") or {}
        lines = [head]
        if ptr.get("what") == "text" and ptr.get("text"):
            shown = ptr["text"] if len(ptr["text"]) <= 200 else ptr["text"][:200] + "..."
            lines.append(f'(pointing at: "{shown}")')
        lines.append("")
        lines.append((r.get("text") or "").strip())
        parts.append("\n".join(lines))
    text = "\n\n".join(parts)
    if limit and len(text) > limit:
        cut = text[-limit:]
        text = "(earlier history is in the file)\n\n" + cut[cut.find("\n## ") + 1 :]
    return text


def slug(group: list[str]) -> str:
    """One file per group, named for its shortest id (the essay, not its map)."""
    first = min(group, key=lambda k: (len(k), k))
    return re.sub(r"[^A-Za-z0-9-]+", "-", first.partition("/")[2]).strip("-").lower() or "untitled"


def write(data_dir: Path, records: list[dict], group: list[str], titles: dict[str, str]) -> Path:
    folder = data_dir / "threads"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug(group)}.md"
    docs = "\n".join(f"- {titles.get(k, k)} ({k})" for k in group)
    full = history(records, group, titles, limit=0) if records else ""
    path.write_text(f"# What the user has said about: {titles.get(group[0], group[0])}\n\nDocuments in this group:\n{docs}\n\n{full}\n")
    return path
