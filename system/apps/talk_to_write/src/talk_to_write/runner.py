"""Talk to Write: talk out loud and turn the best of what you say into writing in your own voice.

Three parts, one page:

- Talk: the workspace chat app's page for one ongoing writing chat, shown in a frame.
  Everything the user says into the mic goes to that chat with where the user is (which list,
  which document) and what the user is pointing at.
- Canvas: documents (markdown) and pages (HTML the agent made) under DATA_DIR.
- Garden: the user's notes folder (an Obsidian-style vault), read and edited in place.

The agent works through the ``talk-to-write`` skill: it edits the Canvas files and
garden notes directly and posts suggested edits to ``/api/suggestions``.

Services run from /home/user/workspace (the repo root). Persistent state lives under
``DATA_DIR`` (``TALK_TO_WRITE_DATA_DIR`` overrides it; never hardcode the path), the
port is ``PORT`` (``TALK_TO_WRITE_PORT``), and the Garden's notes folder is a setting with no
default (``TALK_TO_WRITE_NOTES_FOLDER``, or ``notes_folder`` in ``DATA_DIR/settings.json``;
see ``garden.notes_folder``).
"""

import hashlib
import json
import os
import queue
import re
import secrets
import threading
import time
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    request,
    send_file,
    stream_with_context,
)
from werkzeug.serving import run_simple

from talk_to_write.chat_bridge import ChatBridge, ChatError
from talk_to_write.editor import Editor
from talk_to_write.garden import Garden, GardenError, is_public, notes_folder
from talk_to_write.render import render
from talk_to_write import talkback, threads
from talk_to_write.speaking import SpeakingVoice
from talk_to_write.store import (
    FRONTMATTER,
    Store,
    StoreError,
    body_of,
    item_title,
    set_title,
)
from talk_to_write.versions import Versions

DATA_DIR = Path(os.environ.get("TALK_TO_WRITE_DATA_DIR", "data/.apps/talk-to-write"))
PORT = int(os.environ.get("TALK_TO_WRITE_PORT", "8099"))
APPS_REGISTRY = Path("data/.state/apps.toml")
OWN_NAME = "talk-to-write"

SHELL_STATIC_MODULES_DIR = Path(
    "system/apps/system_interface/imbue/system_interface/static/_static"
)
SHELL_STATIC_MODULE_NAMES = ("app_contract.js", "context_menu.js")
SHELL_PAGE_SCRIPT = """<script type="module">
  import { connectToShell } from "/_static/app_contract.js";
  import { installElementContextMenu } from "/_static/context_menu.js";
  let handshake = null;
  const connection = connectToShell({
    onHandshake: (received) => {
      handshake = received;
      connection.location(location.pathname + location.search + location.hash, document.title);
    },
  });
  installElementContextMenu({ connection, handshake: () => handshake });
  addEventListener("hashchange", () => { if (handshake) connection.location(location.pathname + location.search + location.hash, document.title); });
</script>"""

STATIC_DIR = Path(__file__).parent / "static"
ICON = Path(__file__).resolve().parents[2] / "icon.svg"
SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")

app = Flask("talk_to_write", static_folder=None)
store = Store(DATA_DIR)
garden = Garden(notes_folder(DATA_DIR))
chat = ChatBridge(DATA_DIR / "chat.json")
versions = Versions(DATA_DIR / "versions")
editor = Editor(DATA_DIR)
speaking = SpeakingVoice(DATA_DIR / "voice")
# (side, id) -> mtime of the app's own last write, so the change watcher can tell
# the app's writes (theirs) from everyone else's (the agent's, or another app's).
own_writes: dict[tuple[str, str], float] = {}


def fail(message: str, status: int = 400) -> tuple[Response, int]:
    return jsonify({"error": message}), status


@app.errorhandler(StoreError)
@app.errorhandler(GardenError)
@app.errorhandler(ChatError)
def plain_error(err: Exception) -> tuple[Response, int]:
    return fail(str(err))


# ---------------------------------------------------------------------------
# Page and static files
# ---------------------------------------------------------------------------


def page_version() -> str:
    """Changes whenever the page's files change: a phone never keeps an old copy, and an open
    page reloads itself when it sees a new one."""
    return str(
        int(
            max(
                (STATIC_DIR / n).stat().st_mtime
                for n in ("app.js", "app.css", "editor.js", "index.html")
            )
        )
    )


@app.route("/api/page-version")
def page_version_route() -> Response:
    return jsonify({"version": page_version()})


@app.route("/")
def index() -> Response:
    page = (STATIC_DIR / "index.html").read_text()
    version = page_version()
    # The second pane of side by side is this page in a frame: only the outer page talks to the shell.
    shell = "" if request.args.get("beside") else SHELL_PAGE_SCRIPT
    page = page.replace("{{VERSION}}", version).replace("{{SHELL_PAGE_SCRIPT}}", shell)
    return Response(page, mimetype="text/html", headers={"Cache-Control": "no-cache"})


@app.route("/talkback")
def talkback_page() -> Response:
    """Prototype: a partner that talks back, steered by modes and dials, or a council."""
    page = (STATIC_DIR / "talkback.html").read_text()
    page = page.replace("{{SHELL_PAGE_SCRIPT}}", SHELL_PAGE_SCRIPT)
    return Response(page, mimetype="text/html", headers={"Cache-Control": "no-cache"})


@app.route("/api/talkback", methods=["POST"])
def talkback_reply() -> Response | tuple[Response, int]:
    data = request.get_json(force=True)
    messages = [m for m in data.get("messages", []) if (m.get("text") or "").strip()]
    if not messages or messages[-1].get("role") != "me":
        return fail("Say something first.")
    room = [n.strip() for n in data.get("room", []) if n.strip()][:4]
    out = {
        "mode": data.get("mode", "off"),
        "turns": talkback.discussion(room, data.get("mode", "off"), data.get("dials") or {}, messages),
    }
    if all("error" in t for t in out["turns"]):
        return fail("No answer came back.", 502)
    talkback.keep(DATA_DIR, {"asked": data, **out})
    return jsonify(out)


@app.route("/api/talk-settings")
def get_talk_settings() -> Response:
    return jsonify(talkback.load_settings(DATA_DIR))


@app.route("/api/talk-settings", methods=["PUT"])
def put_talk_settings() -> Response:
    before = talkback.load_settings(DATA_DIR)["room"]
    saved = talkback.save_settings(DATA_DIR, request.get_json(force=True))
    note = talkback.room_note(before, saved["room"])
    if note:
        # Typed messages reach the chat without the app, so tell it who's here now.
        try:
            chat.send(note)
        except ChatError:
            pass  # it reads the settings file on its next message anyway
    return jsonify(saved)


@app.route("/static/<name>")
def static_file(name: str) -> Response:
    if name not in {"app.js", "app.css", "editor.js"}:
        abort(404)
    return send_file((STATIC_DIR / name).absolute(), max_age=0)


@app.route("/icon.svg")
def icon() -> Response:
    return send_file(ICON, mimetype="image/svg+xml")


@app.route("/_static/<basename>")
def shell_module(basename: str) -> Response:
    if basename not in SHELL_STATIC_MODULE_NAMES:
        abort(404)
    module_path = SHELL_STATIC_MODULES_DIR / basename
    if not module_path.is_file():
        abort(404)
    return send_file(module_path.absolute(), mimetype="text/javascript")


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


@app.route("/media/<name>")
def media(name: str) -> Response:
    path = (store.media / name).resolve()
    if path.parent != store.media.resolve() or not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


_attachments: dict[str, Path] = {}


@app.route("/garden-file/<path:name>")
def garden_file(name: str) -> Response:
    """An image or other file a garden note embeds with ![[name]]."""
    key = Path(name).name.lower()
    if key not in _attachments:
        for dirpath, dirnames, filenames in os.walk(garden.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if not f.endswith(".md"):
                    _attachments.setdefault(f.lower(), Path(dirpath) / f)
    path = _attachments.get(key)
    if not path or not path.is_file():
        abort(404)
    return send_file(path, conditional=True)


@app.route("/api/media", methods=["POST"])
def upload_media() -> Response | tuple[Response, int]:
    file = request.files.get("file")
    if not file or not file.filename:
        return fail("No file came through. Try again.")
    name = store.save_media(file.filename, file.read())
    return jsonify({"url": f"/media/{name}"})


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@app.route("/api/canvas")
def canvas_list() -> Response:
    return jsonify({"items": store.items(), "trash": len(store.trash())})


@app.route("/api/canvas/new", methods=["POST"])
def canvas_new() -> Response:
    title = (request.get_json(silent=True) or {}).get("title") or "Untitled"
    return jsonify({"id": store.create_doc(title)})


@app.route("/api/garden")
def garden_list() -> Response:
    if not garden.available():
        return jsonify(
            {"available": False, "connected": garden.connected, "items": [], "trash": 0}
        )
    q = (request.args.get("q") or "").strip().lower()
    notes = garden.search(q) if q else garden.notes()
    items = [
        {"id": n.id, "title": n.title, "public": n.public, "modified": n.modified}
        for n in notes
    ]
    return jsonify({"available": True, "items": items, "trash": len(garden.trash())})


@app.route("/api/garden/resolve")
def resolve_note() -> Response:
    name = (request.args.get("name") or "").strip().lower()
    for n in garden.notes():
        if n.title.lower() == name or n.id.lower() == name:
            return jsonify({"id": n.id})
    return jsonify({"id": None})


@app.route("/api/garden/rename", methods=["POST"])
def rename_note() -> Response:
    """Retitle a garden note: its file, every link to it, and what the app keeps about it."""
    data = request.get_json(force=True)
    old_id = data.get("id", "")
    new_id = garden.rename(old_id, data.get("title", ""))
    if new_id != old_id:
        # Its suggestions, their undo record, and its version history follow it to the new name.
        old_s, new_s = suggestions_file("k", old_id), suggestions_file("k", new_id)
        for suffix in (".json", ".undo.json"):
            src = old_s.with_name(old_s.stem + suffix)
            if src.exists() and not old_s.with_name(new_s.stem + suffix).exists():
                os.replace(src, new_s.with_name(new_s.stem + suffix))
        versions.move("k", old_id, new_id)
        changes.mark("k", new_id)
    # Renaming can rewrite the note's own links to itself: the user keeps typing from this version.
    text = garden.read(new_id)
    return jsonify(
        {
            "id": new_id,
            "modified": garden.path(new_id).stat().st_mtime,
            "body": split_doc(text)[1],
        }
    )


# ---------------------------------------------------------------------------
# One document, page, or garden note
# ---------------------------------------------------------------------------


def stash_undo(side: str, item_id: str, payload: dict) -> str:
    """Keep what's needed to undo one card action; returns its token. The last 30 are kept."""
    path = suggestions_file(side, item_id).with_suffix(".undo.json")
    saved = json.loads(path.read_text()) if path.exists() else {}
    token = secrets.token_hex(6)
    saved[token] = {**json.loads(json.dumps(payload)), "at": time.time()}
    for old in sorted(saved, key=lambda k: saved[k]["at"])[:-30]:
        del saved[old]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, ensure_ascii=False))
    return token


def take_undo(side: str, item_id: str, token: str) -> dict | None:
    path = suggestions_file(side, item_id).with_suffix(".undo.json")
    saved = json.loads(path.read_text()) if path.exists() else {}
    found = saved.pop(token, None)
    if found is not None:
        path.write_text(json.dumps(saved, ensure_ascii=False))
    return found


def suggestions_file(side: str, item_id: str) -> Path:
    digest = hashlib.sha1(f"{side}:{item_id}".encode()).hexdigest()[:16]
    return DATA_DIR / "suggestions" / f"{digest}.json"


def load_suggestions(side: str, item_id: str) -> list[dict]:
    path = suggestions_file(side, item_id)
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("suggestions", [])


def save_suggestions(side: str, item_id: str, items: list[dict]) -> None:
    path = suggestions_file(side, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"side": side, "id": item_id, "suggestions": items},
            indent=1,
            ensure_ascii=False,
        )
    )


def norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def said_in(body: str) -> list[dict]:
    """Which things the user said appear in this document, and when the user said them."""
    doc = norm(body)
    found = []
    for entry in store.transcript():
        for sentence in SENTENCE.findall(entry.get("text", "")):
            s = norm(sentence)
            if len(s) > 12 and s in doc:
                found.append({"text": sentence.strip(), "at": entry["at"]})
    return found


def split_doc(text: str) -> tuple[str, str]:
    """(the property block, the body) of a document or garden note."""
    match = FRONTMATTER.match(text)
    return (text[: match.end()], text[match.end() :]) if match else ("", text)


SOURCE_PROP = re.compile(r"^source:\s*\"?(https?://[^\"\s]+)\"?\s*$", re.M)


def source_url(text: str) -> str | None:
    """Where an imported document came from (its ``source:`` property), to link back to it."""
    front = split_doc(text)[0]
    found = SOURCE_PROP.search(front)
    return found.group(1) if found else None


def write_item(
    side: str, kind: str, item_id: str, text: str, who: str = "you", fresh: bool = False
) -> float:
    """Write a document or note, keeping version history: the state before (if it was never
    recorded) and the new state."""
    old, _ = read_item(side, kind, item_id)
    if not versions.has_any(side, item_id):
        versions.record(side, item_id, old, "earlier")
    modified = (
        garden.write(item_id, text) if side == "k" else store.write(kind, item_id, text)
    )
    own_writes[(side, item_id)] = modified
    versions.record(side, item_id, text, who, fresh=fresh)
    return modified


def read_item(side: str, kind: str, item_id: str) -> tuple[str, float]:
    if side == "k":
        return garden.read(item_id), garden.path(item_id).stat().st_mtime
    return store.read(kind, item_id), store.path(kind, item_id).stat().st_mtime


def doc_stamp(side: str, modified: float, pending: list[dict], item_id: str) -> str:
    """Changes whenever the document, its pending edits, or whether edits are coming changes."""
    ids = ",".join(sorted(s["id"] for s in pending))
    return f"{modified}|{ids}|{int(editor.busy(side, item_id))}"


@app.route("/api/stamp")
def get_stamp() -> Response:
    """A cheap check the open page makes every few seconds, in case a live update was missed."""
    side, kind, item_id = (
        request.args.get("side", "canvas"),
        request.args.get("kind", "doc"),
        request.args.get("id", ""),
    )
    try:
        _, modified = read_item(side, kind, item_id)
    except (OSError, ValueError, KeyError):
        return jsonify({"stamp": "gone"})
    pending = [
        s for s in load_suggestions(side, item_id) if s.get("status") == "pending"
    ]
    return jsonify({"stamp": doc_stamp(side, modified, pending, item_id)})


@app.route("/api/doc")
def get_doc() -> Response:
    side, kind, item_id = (
        request.args.get("side", "canvas"),
        request.args.get("kind", "doc"),
        request.args.get("id", ""),
    )
    text, modified = read_item(side, kind, item_id)
    if not versions.has_any(side, item_id):
        versions.record(side, item_id, text, "earlier")
    if side == "canvas" and kind == "page":
        return jsonify(
            {
                "kind": "page",
                "title": item_title("page", text, item_id),
                "source": text,
                "modified": modified,
            }
        )
    if side == "k":
        body, title, public = split_doc(text)[1], Path(item_id).name, is_public(text)
    else:
        body, title, public = (
            body_of(text).lstrip("\n"),
            item_title("doc", text, item_id),
            False,
        )
    pending = [
        s for s in load_suggestions(side, item_id) if s.get("status") == "pending"
    ]
    return jsonify(
        {
            "kind": "note" if side == "k" else "doc",
            "title": title,
            "body": body,
            "html": render(text, garden=side == "k"),
            "public": public,
            "modified": modified,
            "said": said_in(body),
            "source_url": source_url(text),
            "suggestions": pending,
            "suggesting": editor.busy(side, item_id),
            "stamp": doc_stamp(side, modified, pending, item_id),
        }
    )


@app.route("/api/doc", methods=["PUT"])
def put_doc() -> Response | tuple[Response, int]:
    data = request.get_json(force=True)
    side, kind, item_id, body = (
        data.get("side"),
        data.get("kind"),
        data.get("id", ""),
        data.get("body", ""),
    )
    text, modified = read_item(side, kind, item_id)
    base = data.get("base")
    if base is not None and modified > float(base) + 0.01:
        return fail(
            "This changed while you were editing (the agent may have just edited it). Copy anything you need, then tap Done to see the latest.",
            409,
        )
    front = split_doc(text)[0]
    if side == "k":
        return jsonify({"modified": write_item(side, kind, item_id, front + body)})
    new = front + "\n" + body.lstrip("\n")
    new = set_title(new, (data.get("title") or "Untitled").strip())
    return jsonify({"modified": write_item(side, kind, item_id, new)})


# ---------------------------------------------------------------------------
# Suggested edits (posted by the agent, accepted or skipped by them)
# ---------------------------------------------------------------------------


suggestions_lock = threading.Lock()


def add_edits(side: str, item_id: str, edits: list[dict]) -> None:
    """A section's edits are ready: add them to the document's pending suggestions."""
    with suggestions_lock:
        items = load_suggestions(side, item_id)
        # A new round adds ideas next to the earlier ones; only an exact repeat is left out.
        def same(e: dict) -> tuple:
            return (e["find"], tuple(e.get("options") or [e.get("replace", "")]))

        taken = {same(i) for i in items if i.get("status") == "pending"}
        for e in edits:
            if same(e) in taken:
                continue
            taken.add(same(e))
            items.append(
                {
                    **e,
                    "id": secrets.token_hex(4),
                    "status": "pending",
                    "at": time.time(),
                }
            )
        save_suggestions(side, item_id, items)
    changes.mark(side, item_id)


@app.route("/api/suggest", methods=["POST"])
def suggest() -> Response | tuple[Response, int]:
    """Start voice edits on a document, or on one passage of it (`scope`). Returns at once;
    edits appear as each section finishes (the page hears about it on /api/events)."""
    data = request.get_json(force=True)
    side, kind, item_id = (
        data.get("side", "canvas"),
        data.get("kind", "doc"),
        data.get("id", ""),
    )
    scope = (data.get("scope") or "").strip() or None
    if editor.busy(side, item_id):
        return fail("Edits for this are already on their way.")
    text, _ = read_item(side, kind, item_id)
    body = split_doc(text)[1]
    if scope and scope not in body:
        # A pointed-at passage arrives as rendered text; find the paragraph it came from.
        want = norm(scope)
        scope = next(
            (p for p in re.split(r"\n\s*\n", body) if want and want in norm(p)), None
        )
        if not scope:
            return fail("Couldn't find that passage in the document.")
    title = Path(item_id).name if side == "k" else item_title("doc", text, item_id)
    def done(count: int, error: str | None) -> None:
        hub.publish(
            {
                "type": "suggesting",
                "side": side,
                "id": item_id,
                "state": "done",
                "count": count,
                "error": error,
            }
        )
        changes.mark(side, item_id)

    n = editor.start(
        side,
        item_id,
        title,
        body,
        scope,
        bool(data.get("small")),
        lambda edits: add_edits(side, item_id, edits),
        done,
    )
    hub.publish({"type": "suggesting", "side": side, "id": item_id, "state": "started"})
    return jsonify({"started": True, "sections": n})


@app.route("/api/proposals", methods=["POST"])
def add_proposals() -> Response | tuple[Response, int]:
    """The writing chat proposes changes for them to accept: {side, kind, id, changes: [{find, replace, why}]}.
    `find` is exact text in the document (empty to add at the end); `replace` is what goes there
    (empty to cut it)."""
    data = request.get_json(force=True)
    side, kind, item_id = (
        data.get("side", "canvas"),
        data.get("kind", "doc"),
        data.get("id", ""),
    )
    text, _ = read_item(side, kind, item_id)
    body = split_doc(text)[1]
    added, missing = [], []
    with suggestions_lock:
        items = load_suggestions(side, item_id)
        for c in data.get("changes", []):
            find, replace = c.get("find", ""), c.get("replace", "")
            if find and find not in body:
                missing.append(find)
                continue
            if not find and not replace.strip():
                continue
            entry = {
                "source": "chat",
                "kind": "chat",
                "find": find,
                "options": [replace],
                "replace": replace,
                "why": c.get("why", ""),
                "id": secrets.token_hex(4),
                "status": "pending",
                "at": time.time(),
            }
            items.append(entry)
            added.append(entry["id"])
        save_suggestions(side, item_id, items)
    changes.mark(side, item_id)
    return jsonify({"added": len(added), "not_found": missing})


@app.route("/api/suggestion", methods=["POST"])
def decide_suggestion() -> Response | tuple[Response, int]:
    data = request.get_json(force=True)
    side, kind, item_id, sid, action = (
        data.get("side"),
        data.get("kind"),
        data.get("id", ""),
        data.get("sid"),
        data.get("action"),
    )
    items = load_suggestions(side, item_id)
    target = next((s for s in items if s["id"] == sid), None)
    if not target:
        return fail("That suggestion is gone.")
    if action == "undo":
        # Put an accepted change's words back the way they were, and the change back on the page.
        if target.get("status") == "accepted":
            text, _ = read_item(side, kind, item_id)
            front, body = split_doc(text)
            undo = target.get("undo")
            if (
                undo
                and body[undo["start"] : undo["start"] + len(undo["new"])]
                == undo["new"]
            ):
                restored = (
                    body[: undo["start"]]
                    + undo["old"]
                    + body[undo["start"] + len(undo["new"]) :]
                )
            else:
                return fail(
                    "That part has changed since, so it can't be undone here. Version history has the earlier text."
                )
            write_item(side, kind, item_id, front + restored, "you", fresh=True)
            versions.seal(side, item_id)
        target["status"] = "pending"
        target.pop("chosen", None)
        target.pop("undo", None)
        for other in items:
            # Ideas set aside when the user picked this one come back too.
            if other.get("with") == target["id"]:
                other["status"] = "pending"
                other.pop("with", None)
        save_suggestions(side, item_id, items)
        return jsonify({"ok": True})
    if action == "undo_snapshot":
        # Undo a card action: put the words back and every suggestion as it was just before.
        saved = take_undo(side, item_id, str(data.get("token", "")))
        if not saved:
            return fail("That can't be undone any more.")
        if saved.get("old") is not None:
            text, _ = read_item(side, kind, item_id)
            front, body = split_doc(text)
            at, new = saved["start"], saved["new"]
            if body[at : at + len(new)] != new:
                found = body.find(new)
                if found < 0:
                    return fail(
                        "That part has changed since, so it can't be undone here. Version history has the earlier text."
                    )
                at = found
            write_item(side, kind, item_id, front + body[:at] + saved["old"] + body[at + len(new) :], "you", fresh=True)
            versions.seal(side, item_id)
        save_suggestions(side, item_id, saved["items"])
        return jsonify({"ok": True})
    if action == "accept_group":
        # Several suggestions for the same words; the user switched on any of them (`applied`, each a
        # {sid, find, rep} piece, in order) and maybe changed the result. `text` replaces the whole
        # stretch they cover, and that spot is done: what the user took is accepted, the rest set aside.
        group = [g for g in items if g["id"] in set(data.get("sids") or [])]
        text, _ = read_item(side, kind, item_id)
        front, body = split_doc(text)
        spans = [(body.find(g["find"]), len(g["find"])) for g in group if g.get("find")]
        if not spans or any(at < 0 for at, _ in spans):
            return fail("That text has changed since these were suggested.")
        start = min(at for at, _ in spans)
        end = max(at + n for at, n in spans)
        old, new = body[start:end], str(data.get("text", ""))
        token = stash_undo(side, item_id, {"start": start, "old": old, "new": new, "items": items})
        items = json.loads(json.dumps(items))
        write_item(side, kind, item_id, front + body[:start] + new + body[end:], "you", fresh=True)
        versions.seal(side, item_id)
        applied = [a for a in data.get("applied") or [] if isinstance(a, dict)]
        used = {piece.get("sid") for piece in applied}
        in_group = {g["id"] for g in group}
        for g in items:
            if g["id"] in in_group:
                g["status"] = "accepted" if g["id"] in used else "skipped"
                g["decided"] = time.time()
            elif g.get("status") == "pending" and g.get("find"):
                # Still waiting: follow the words the user just changed, so it still points at them.
                for piece in applied:
                    f, rep = piece.get("find", ""), piece.get("rep", "")
                    if f and f in g["find"]:
                        g["find"] = g["find"].replace(f, rep, 1)
                        for fix in g.get("small") or []:
                            if f in fix["from"]:
                                fix["from"] = fix["from"].replace(f, rep, 1)
        save_suggestions(side, item_id, items)
        return jsonify({"ok": True, "token": token})
    if action == "skip_piece":
        # Set aside one change from the card: a chat change, one small fix, or one rewrite.
        token = stash_undo(side, item_id, {"old": None, "items": items})
        items = json.loads(json.dumps(items))
        g = next(i for i in items if i["id"] == sid)
        f, rep = data.get("find", ""), data.get("rep", "")
        if g.get("source") == "chat":
            g["status"] = "skipped"
        else:
            g["small"] = [x for x in g.get("small") or [] if not (x["from"] == f and x["to"] == rep)]
            g["rewrites"] = [x for x in g.get("rewrites") or g.get("options") or [] if not (f == g["find"] and x == rep)]
            g["options"] = g["rewrites"]
            if not g["small"] and not g["rewrites"]:
                g["status"] = "skipped"
        g["decided"] = time.time()
        save_suggestions(side, item_id, items)
        return jsonify({"ok": True, "token": token})
    if action == "accept":
        text, _ = read_item(side, kind, item_id)
        front, body = split_doc(text)
        options = target.get("options") or [target.get("replace", "")]
        choice = int(data.get("option") or 0)
        if not 0 <= choice < len(options):
            return fail("That option isn't there.")
        own = data.get("text")
        if isinstance(own, str):
            # The user changed the suggestion before accepting it: their words go in.
            options = [*options[:choice], own, *options[choice + 1 :]]
            target["edited"] = own
        if target["find"] == "":
            # An addition at the end of the document.
            start = len(body.rstrip("\n"))
            old = body[start:]
            new = ("\n\n" if start else "") + options[choice].strip("\n") + "\n"
        else:
            start = body.find(target["find"])
            if start < 0:
                return fail("That text has changed since the suggestion was made.")
            old, new = target["find"], options[choice]
        write_item(
            side,
            kind,
            item_id,
            front + body[:start] + new + body[start + len(old) :],
            "you",
            fresh=True,
        )
        versions.seal(side, item_id)
        target["undo"] = {"start": start, "old": old, "new": new}
    target["status"] = "accepted" if action == "accept" else "skipped"
    if action == "accept":
        target["chosen"] = int(data.get("option") or 0)
    target["decided"] = time.time()
    save_suggestions(side, item_id, items)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------


@app.route("/api/versions")
def list_versions() -> Response:
    side, item_id = request.args.get("side", "canvas"), request.args.get("id", "")
    return jsonify({"versions": versions.list(side, item_id)})


@app.route("/api/version")
def get_version() -> Response | tuple[Response, int]:
    side, item_id, vid = (
        request.args.get("side", "canvas"),
        request.args.get("id", ""),
        request.args.get("vid", ""),
    )
    try:
        text = versions.text(side, item_id, vid)
    except KeyError:
        return fail("That version isn't there.")
    return jsonify(
        {
            "html": render(text, garden=side == "k"),
            "title": item_title("doc", text, item_id)
            if side != "k"
            else Path(item_id).name,
        }
    )


@app.route("/api/version/restore", methods=["POST"])
def restore_version() -> Response | tuple[Response, int]:
    data = request.get_json(force=True)
    side, kind, item_id, vid = (
        data.get("side"),
        data.get("kind"),
        data.get("id", ""),
        str(data.get("vid", "")),
    )
    try:
        text = versions.text(side, item_id, vid)
    except KeyError:
        return fail("That version isn't there.")
    versions.seal(side, item_id)
    write_item(side, kind, item_id, text, "restored", fresh=True)
    versions.seal(side, item_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Trash
# ---------------------------------------------------------------------------


@app.route("/api/trash")
def trash_list() -> Response:
    side = request.args.get("side", "canvas")
    return jsonify({"items": garden.trash() if side == "k" else store.trash()})


@app.route("/api/trash", methods=["POST"])
def trash_item() -> Response:
    data = request.get_json(force=True)
    if data.get("side") == "k":
        return jsonify({"name": garden.delete(data["id"])})
    return jsonify({"name": store.delete(data.get("kind", "doc"), data["id"])})


@app.route("/api/trash/restore", methods=["POST"])
def trash_restore() -> Response:
    data = request.get_json(force=True)
    if data.get("side") == "k":
        return jsonify({"id": garden.restore(data["name"])})
    return jsonify(store.restore(data["name"]))


@app.route("/api/trash/purge", methods=["POST"])
def trash_purge() -> Response:
    data = request.get_json(force=True)
    (garden.purge if data.get("side") == "k" else store.purge)(data["name"])
    return jsonify({"ok": True})


@app.route("/api/trash/empty", methods=["POST"])
def trash_empty() -> Response:
    data = request.get_json(force=True)
    (garden.empty_trash if data.get("side") == "k" else store.empty_trash)()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Talking: what the user says goes to the chat with where the user is
# ---------------------------------------------------------------------------


def describe(context: dict) -> str:
    """One header line (or a few) telling the agent where the user is and what the user is pointing at.

    With two documents side by side, the second pane's screen follows, indented under its own line.
    """
    text = describe_pane(context)
    beside = context.get("beside")
    if isinstance(beside, dict):
        text += "\nAlso open side by side, in the right-hand pane:\n" + "\n".join(
            "  " + line for line in describe_pane(beside).split("\n")
        )
    return text


def describe_pane(context: dict) -> str:
    """Where the user is in one pane, and what the user is pointing at there."""
    side = context.get("side", "canvas")
    where = "Garden (notes)" if side == "garden" else "Canvas"
    view = context.get("view", "list")
    item = context.get("item") or {}
    lines = []
    if view in ("doc", "page") and item:
        if side == "garden":
            file = garden.root / f"{item.get('id')}.md"
            lines.append(
                f'Screen: {where} > garden note "{item.get("title")}" (file: {file})'
            )
        else:
            ext = ".html" if item.get("kind") == "page" else ".md"
            file = (DATA_DIR / "canvas" / f"{item.get('id')}{ext}").resolve()
            label = "page" if item.get("kind") == "page" else "document"
            lines.append(
                f'Screen: {where} > {label} "{item.get("title")}" (side=canvas kind={item.get("kind")} id={item.get("id")}, file: {file})'
            )
    elif view == "trash":
        lines.append(f"Screen: {where} > Trash")
    else:
        lines.append(
            f"Screen: {where} list"
            + (f' (searching "{context["search"]}")' if context.get("search") else "")
        )
    if context.get("tab") == "talk":
        lines[0] += " (the user is looking at this chat; that was the last thing open)"
    ptr = context.get("pointing")
    if ptr:
        if ptr.get("what") == "text":
            lines.append(f'Pointing at: "{ptr.get("text")}"')
            if ptr.get("in_passage") and ptr.get("in_passage") != ptr.get("text"):
                lines.append(f'  in the passage: "{ptr.get("in_passage")}"')
        else:
            cap = f' captioned "{ptr.get("caption")}"' if ptr.get("caption") else ""
            lines.append(
                f"Pointing at: the {ptr.get('what')} {ptr.get('src', '')}{cap}"
            )
    return "\n".join(lines)


@app.route("/api/say", methods=["POST"])
def say() -> Response | tuple[Response, int]:
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    context = data.get("context") or {}
    if not text:
        return fail("Nothing was heard.")
    how = data.get("how", "spoken")
    entry = store.record(text, context, how)
    if how == "spoken":
        speaking.add(entry)
    write_screen(context)
    if context.get("action") == "suggest-edits":
        header = "[Talk to Write | the user tapped Suggest edits]"
    else:
        header = f"[Talk to Write | {how} {entry['at']}]"
    steer = talkback.chat_lines(talkback.load_settings(DATA_DIR))
    whole = whole_document(context)
    past = history_on_switch(context)
    chat.send(
        f"{header}\n{describe(context)}"
        + (f"\n{steer}" if steer else "")
        + (f"\n{past}" if past else "")
        + (f"\n{whole}" if whole else "")
        + f"\n\n{text}"
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# One running history per piece of writing (see threads.py)
# ---------------------------------------------------------------------------


def known_documents(records: list[dict]) -> tuple[set[str], dict[str, str]]:
    """Every document there is or that the user has talked about, and their titles."""
    titles: dict[str, str] = {}
    for r in records:
        for pane in (r.get("context") or {}, (r.get("context") or {}).get("beside") or {}):
            item = pane.get("item") or {} if isinstance(pane, dict) else {}
            if item.get("id") and item.get("title"):
                titles[threads.key(pane.get("side", "canvas"), item["id"])] = item["title"]
    known = set(titles)
    for path in (DATA_DIR / "canvas").glob("*"):
        if path.suffix in (".md", ".html") and not path.name.endswith(".data.json"):
            known.add(f"canvas/{path.stem}")
    return known, titles


def groups_for(context: dict, records: list[dict]) -> list[list[str]]:
    known, _ = known_documents(records)
    return [threads.group_of(DATA_DIR, k, known | {k}) for k in threads.where(context)]


def refresh_threads(context: dict) -> list[Path]:
    """Rewrite the history file of each document group this message was about."""
    records = store.transcript()
    _, titles = known_documents(records)
    return [threads.write(DATA_DIR, records, group, titles) for group in groups_for(context, records)]


def history_on_switch(context: dict) -> str:
    """When the user has just come to a document from somewhere else, everything the user has said
    about it and the documents that belong with it, so the chat picks the piece back up."""
    records = store.transcript()
    refresh_threads(context)
    groups = groups_for(context, records)
    if not groups:
        return ""
    before = [r for r in records[:-1] if threads.where(r.get("context") or {})]
    if before and set(threads.where(before[-1].get("context") or {})) & set(groups[0]):
        return ""
    _, titles = known_documents(records)
    path = (DATA_DIR / "threads" / f"{threads.slug(groups[0])}.md").resolve()
    line = f"The user has just come back to this piece from other work. Before replying, read everything the user has said about it, oldest first: {path}"
    others = groups[0][1:]
    if others:
        line += "\nIt belongs with: " + "; ".join(f"{titles.get(k, k)} ({k})" for k in others) + ". Read those too when they bear on what the user asks."
    return line


def record_typed(text: str) -> None:
    """Something the user typed straight into the chat: kept with where the user was, like what the user says."""
    screen_file = DATA_DIR / "screen.json"
    context = json.loads(screen_file.read_text()).get("context", {}) if screen_file.exists() else {}
    store.record(text, context, "typed")
    refresh_threads(context)


@app.route("/api/related", methods=["POST"])
def relate() -> Response | tuple[Response, int]:
    """Link documents so they share one history: {"items": ["canvas/<id>", "k/<note path>", ...]}."""
    items = [str(i) for i in (request.get_json(force=True) or {}).get("items", []) if str(i).startswith(("canvas/", "k/"))]
    if len(items) < 2:
        return fail("Name at least two documents to link.")
    return jsonify({"groups": threads.link(DATA_DIR, items)})


def whole_document(context: dict) -> str:
    """A line asking the chat to read the document the user has open before it replies (the file is
    on the Screen line), so every suggestion is made with the whole piece in view. The text
    itself isn't pasted: it would fill their chat. "" when no document is open."""
    item = context.get("item") or {}
    if context.get("view") != "doc" or item.get("kind", "doc") not in ("doc", "note") or not item.get("id"):
        return ""
    return "Before replying, read the whole document as it is now (the file on the Screen line)."


def write_screen(context: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"at": time.time(), "summary": describe(context), "context": context}
    (DATA_DIR / "screen.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False)
    )


@app.route("/api/screen", methods=["POST"])
def screen() -> Response:
    write_screen(request.get_json(force=True) or {})
    return jsonify({"ok": True})


def own_label(name: str) -> str | None:
    if not APPS_REGISTRY.exists():
        return None
    import tomllib

    for row in tomllib.loads(APPS_REGISTRY.read_text()).get("apps", []):
        if row.get("name") == name:
            return row.get("label")
    return None


@app.route("/api/chat")
def chat_info() -> Response | tuple[Response, int]:
    try:
        chat_id = chat.chat_id()
    except ChatError as err:
        return fail(f"Your writing chat couldn't start. {err}", 503)
    return jsonify(
        {
            "chat_id": chat_id,
            "own_label": own_label(OWN_NAME) or OWN_NAME,
            "chat_label": own_label("chat") or "chat",
            "local_url": "http://localhost:8010",
        }
    )


# ---------------------------------------------------------------------------
# Live updates for the page: replies to read aloud, and files that changed
# ---------------------------------------------------------------------------


class Hub:
    def __init__(self) -> None:
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()

    def publish(self, event: dict) -> None:
        with self.lock:
            for q in list(self.clients):
                q.put(event)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


hub = Hub()


class Changes:
    """Watches the Canvas folder and the garden for changes (mostly the agent's edits)."""

    def __init__(self) -> None:
        self.seen: dict[tuple[str, str], float] = {}

    def mark(self, side: str, item_id: str) -> None:
        hub.publish({"type": "changed", "side": side, "id": item_id})

    def snapshot(self) -> dict[tuple[str, str], float]:
        snap: dict[tuple[str, str], float] = {}
        if store.canvas.is_dir():
            for path in store.canvas.iterdir():
                if path.suffix in (".md", ".html") and not path.name.startswith("."):
                    snap[("canvas", path.stem)] = path.stat().st_mtime
        if garden.available():
            stats = garden.scan()
            garden.refresh(stats)
            snap.update({("k", note_id): mtime for note_id, mtime in stats.items()})
        return snap

    def record_outside_change(self, side: str, item_id: str) -> None:
        """Someone other than their editor changed it (usually the agent): keep that as a version."""
        try:
            if side == "k":
                text = garden.read(item_id)
            else:
                kind = "doc" if store.path("doc", item_id).exists() else "page"
                text = store.read(kind, item_id)
        except (StoreError, GardenError, OSError):
            return
        versions.record(side, item_id, text, "outside")

    def run(self) -> None:
        self.seen = self.snapshot()
        while True:
            time.sleep(1.5)
            try:
                now = self.snapshot()
            except OSError:
                continue
            for key in set(now) | set(self.seen):
                if now.get(key) != self.seen.get(key):
                    self.mark(*key)
                    if key in now and own_writes.get(key) != now[key]:
                        self.record_outside_change(*key)
            self.seen = now


changes = Changes()


@app.route("/api/events")
def events() -> Response:
    def stream():
        q = hub.subscribe()
        try:
            yield "retry: 3000\n\n"
            yield f"data: {json.dumps({'type': 'hello', 'version': page_version()})}\n\n"
            while True:
                try:
                    event = q.get(timeout=20)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(q)

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def start_background() -> None:
    store.ensure()
    speaking.backfill(store.transcript())
    threading.Thread(target=changes.run, daemon=True, name="changes").start()
    chat.on_typed(record_typed)
    chat.on_reply(lambda text: hub.publish({"type": "reply", "text": text}))


def main() -> None:
    start_background()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
