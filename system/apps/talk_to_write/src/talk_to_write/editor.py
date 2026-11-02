"""Voice edits: line edits in the user's own voice, made fast.

One Claude call per section of the document, run in parallel, each with
everything it needs already in the prompt: what makes a good edit (the shared
editing guide), what the user has said out loud to the app about the piece (dictation,
word for word), and their own raw writing -- all their finished essays and site pages,
plus the garden notes most like that section (for style; their rough private notes are
left out, since they show how the user jots, not how the user writes at their best). Each call
also sees the whole piece, so its edits serve what the section is doing in it. No summary of their style: the model reads their words directly. No tool use and no back-and-forth, so a section's edits arrive in
a minute or two and appear in the document as each section finishes.

Runs keyless on their Claude subscription through ``claude -p`` (see the
use-ai-integration skill; ``claude_p.py`` is its helper, copied in).
"""

import json
import logging
import math
import re
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from talk_to_write import claude_p

# Which model and how hard it thinks. Opus for judgment about voice; high effort, since
# mediocre edits cost them more than a slower wait does.
EDIT_MODEL = "opus"
EDIT_EFFORT = "high"
PARALLEL_SECTIONS = 3
SECTION_WORDS = 700
GUIDE = Path(".agents/skills/suggest-voice-edits/editing-guide.md")
ALWAYS_KINDS = {"essay", "page"}  # their finished pieces: small, so every prompt gets all of them
# Per section, the passages most like it, from their curated writing only (garden notes).
# Style comes only from their writing; what the user said aloud is given separately, for meaning.
NEAREST = {"garden": 24}
NOTE_KINDS = {"obsidian", "logseq"}
NOTE_MIN_WORDS = 60  # paragraphs of thought, not fragments, to-dos, or link lists
# What the user has said to the app (dictation, word for word): all of it about this piece first.
SAID_HERE = 10  # messages said while on this document, most like the section first
SAID_ELSEWHERE = 4  # messages said about other things that share the most words with it
SAID_MAX_WORDS = 600  # a long message is cut to its part most like the section
STOP = set(
    "about above after again against also although always among another anyone because been before being below between both "
    "cannot could does doing down during each even every from further have having here theirs themselves himself into itself just "
    "more most much must myself never only other ought ours ourselves over same should some such than that their theirs them "
    "themselves then there these they this those through under until very were what when where which while whom will with "
    "would your yours yourself yourselves really things thing like make made many people other want going know think".split()
)
WORD = re.compile(r"[a-z][a-z']{3,}")
HEADING = re.compile(r"^#{1,4} ")

SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["ai", "concrete", "landing", "rhythm", "trim"],
                    },
                    "find": {"type": "string"},
                    "small": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string"},
                                "to": {"type": "string"},
                            },
                            "required": ["from", "to"],
                        },
                    },
                    "rewrites": {"type": "array", "items": {"type": "string"}},
                    "why": {"type": "string"},
                    "quote": {"type": "string"},
                    "source_title": {"type": "string"},
                },
                "required": [
                    "kind",
                    "find",
                    "small",
                    "rewrites",
                    "why",
                    "quote",
                    "source_title",
                ],
            },
        }
    },
    "required": ["edits"],
}

log = logging.getLogger(__name__)


def words_of(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if w not in STOP}


class PassageBank:
    """Their own writing, grouped: finished pieces, garden notes, private notes (a subset), speech."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._loaded: tuple[float, dict[str, list[tuple[dict, set[str]]]]] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def group_of(p: dict) -> str | None:
        kind, text = p.get("kind"), p.get("text", "")
        if kind in ALWAYS_KINDS:
            return "always"
        if kind in ("garden", "spoken"):
            return kind
        if kind == "claude-chat" and len(text.split()) >= 40:
            return "chats"
        if kind in NOTE_KINDS and len(text.split()) >= NOTE_MIN_WORDS:
            if "TODO" in text or text.count("[[") > 3 or text.count("http") > 1:
                return None
            return "notes"
        return None

    def groups(self) -> dict[str, list[tuple[dict, set[str]]]]:
        with self._lock:
            mtime = self.path.stat().st_mtime if self.path.exists() else 0.0
            if self._loaded is None or self._loaded[0] != mtime:
                groups: dict[str, list[tuple[dict, set[str]]]] = {}
                if self.path.exists():
                    for line in self.path.open():
                        p = json.loads(line)
                        group = self.group_of(p)
                        if group:
                            groups.setdefault(group, []).append((p, words_of(p["text"])))
                self._loaded = (mtime, groups)
            return self._loaded[1]

    def for_section(self, text: str) -> list[dict]:
        """All their finished pieces, then the nearest passages of each other kind."""
        groups = self.groups()
        chosen = [p for p, _ in groups.get("always", [])]
        want = words_of(text)
        for group, k in NEAREST.items():
            scored = []
            for p, ws in groups.get(group, []):
                overlap = len(want & ws)
                if overlap:
                    scored.append((overlap / math.sqrt(len(ws) + 5), p))
            scored.sort(key=lambda s: s[0], reverse=True)
            chosen += [p for _, p in scored[:k]]
        return chosen


def said_about(transcript: Path, item_id: str, section: str) -> list[dict]:
    """What the user has said to the app that bears on this section: everything said while on this
    document (most like the section first), then the closest things said elsewhere."""
    if not transcript.exists():
        return []
    want = words_of(section)
    here, elsewhere = [], []
    for line in transcript.open():
        entry = json.loads(line)
        text = entry.get("text", "").strip()
        if not text:
            continue
        item = (entry.get("context") or {}).get("item") or {}
        score = len(want & words_of(text)) / math.sqrt(len(text.split()) + 5)
        row = (score, {"text": trim_to(text, want), "at": entry.get("at", ""), "title": item.get("title", "")})
        (here if item.get("id") == item_id else elsewhere).append(row)
    here.sort(key=lambda r: r[0], reverse=True)
    elsewhere = [r for r in elsewhere if r[0] > 0]
    elsewhere.sort(key=lambda r: r[0], reverse=True)
    return [r[1] for r in here[:SAID_HERE]] + [r[1] for r in elsewhere[:SAID_ELSEWHERE]]


def trim_to(text: str, want: set[str]) -> str:
    """A long message cut to the stretch of it most like the section."""
    words = text.split()
    if len(words) <= SAID_MAX_WORDS:
        return text
    best, best_at = -1, 0
    for at in range(0, len(words) - SAID_MAX_WORDS + 1, 50):
        score = len(want & words_of(" ".join(words[at : at + SAID_MAX_WORDS])))
        if score > best:
            best, best_at = score, at
    return "... " + " ".join(words[best_at : best_at + SAID_MAX_WORDS]) + " ..."


def sections(body: str) -> list[str]:
    """Split a document at its headers, then into chunks of about SECTION_WORDS words."""
    parts: list[list[str]] = [[]]
    for para in re.split(r"\n\s*\n", body):
        if HEADING.match(para.strip()) and parts[-1]:
            parts.append([])
        parts[-1].append(para)
    chunks = []
    for part in parts:
        current: list[str] = []
        count = 0
        for para in part:
            n = len(para.split())
            if current and count + n > SECTION_WORDS:
                chunks.append("\n\n".join(current))
                current, count = [], 0
            current.append(para)
            count += n
        if current:
            chunks.append("\n\n".join(current))
    # Fold a tiny chunk (a lone header, a one-liner) into the next one.
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1].split()) < 40:
            merged[-1] = merged[-1] + "\n\n" + chunk
        else:
            merged.append(chunk)
    return [
        m
        for m in merged
        if m.strip() and not re.fullmatch(r"\[\^\d+\]:.*", m.strip(), re.S)
    ]


LABELS = {
    "essay": "essay",
    "page": "their website",
    "garden": "garden note",
    "obsidian": "private note",
    "logseq": "private note",
    "spoken": "said aloud",
    "claude-chat": "their message in a chat",
}


def build_prompt(
    section: str,
    title: str,
    passages: list[dict],
    said: list[dict],
    small_only: bool,
    whole: str = "",
) -> str:
    spoken = "\n\n".join(
        f"[{d['at']}{' — while on ' + repr(d['title']) if d['title'] else ''}]\n{d['text']}"
        for d in said
    )
    shown = "\n\n".join(
        f'[{LABELS.get(p.get("kind", ""), p.get("kind"))}] "{p.get("title")}"{" (" + str(p.get("date")) + ")" if p.get("date") else ""}\n{p["text"]}'
        for p in passages
    )
    ask = (
        "First work out what the whole piece argues and what this section has to do for it. Then suggest "
        "only the edits to this section that make it clearly better at that, following the editing guide, "
        "most valuable first. No edits is a fine answer for a strong section."
        if not small_only
        else "The user asked for small edits only this time: give `small` fixes and leave `rewrites` empty."
    )
    said_block = (
        "# What the user means: what the user has said out loud about this piece (phone dictation to the "
        "app, rough and mis-heard in places). Read it for their ideas, not their wording.\n\n"
        f"{spoken}\n\n"
        if spoken
        else ""
    )
    whole_block = (
        f'# The whole piece, for context (edit only the section below)\n\n<piece>\n{whole}\n</piece>\n\n'
        if whole.strip() and whole.strip() != section.strip()
        else ""
    )
    style_block = (
        "# Their own writing, raw (read it for their style: how the user writes at their best)\n\n"
        f"{shown}\n\n"
        if shown
        else "# Their own writing: none is available yet, so take their style from the draft itself.\n\n"
    )
    quote_rule = (
        "For each edit, `quote` is a short verbatim line "
        "from one of the passages above that shows the same kind of move, with its title as `source_title`; "
        "leave both empty only when no passage shows it."
        if shown
        else "Leave `quote` and `source_title` empty: there are no passages to quote."
    )
    return (
        style_block
        + said_block
        + whole_block
        + f'# The draft: a section of "{title}"\n\n'
        f"<section>\n{section}\n</section>\n\n"
        f"{ask} "
        "`find` must be copied exactly from inside <section>. "
        + quote_rule
        + (
            " Use what the user said out loud only for meaning: where the draft misses or blurs an idea, a "
            "specific, or a step the user clearly meant, write it in the style of their written work. Never move "
            "the draft's wording toward how the user said it, and never offer a spoken phrase back for an idea the "
            "draft already expresses: the draft is usually the better-worded version. "
            "`quote` always comes from their written passages, never from what the user said aloud."
            if spoken
            else ""
        )
    )


class Editor:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.bank = PassageBank(data_dir / "voice" / "passages.jsonl")
        self.pool = ThreadPoolExecutor(
            max_workers=PARALLEL_SECTIONS, thread_name_prefix="voice-edits"
        )
        self.running: dict[
            tuple[str, str], int
        ] = {}  # (side, id) -> sections still to come
        self._lock = threading.Lock()

    def busy(self, side: str, item_id: str) -> bool:
        return self.running.get((side, item_id), 0) > 0

    def start(
        self,
        side: str,
        item_id: str,
        title: str,
        body: str,
        scope: str | None,
        small_only: bool,
        on_edits: Callable[[list[dict]], None],
        on_done: Callable[[int, str | None], None],
    ) -> int:
        """Begin edits for the whole body (or just `scope`). Calls on_edits per section, then on_done once."""
        chunks = [scope] if scope else sections(body)
        if not chunks:
            on_done(0, None)
            return 0
        guide = GUIDE.read_text()
        key = (side, item_id)
        with self._lock:
            self.running[key] = self.running.get(key, 0) + len(chunks)
        state = {"count": 0, "errors": []}

        def run(chunk: str) -> None:
            try:
                edits = self.edit_section(chunk, title, guide, small_only, item_id, body)
                state["count"] += len(edits)
                if edits:
                    on_edits(edits)
            except Exception as err:  # one section failing shouldn't stop the others
                log.exception("voice edits failed for a section")
                state["errors"].append(str(err))
            finally:
                with self._lock:
                    self.running[key] -= 1
                    left = self.running[key]
                if left == 0:
                    on_done(
                        state["count"],
                        state["errors"][0]
                        if state["errors"] and not state["count"]
                        else None,
                    )

        for chunk in chunks:
            self.pool.submit(run, chunk)
        return len(chunks)

    def edit_section(
        self,
        section: str,
        title: str,
        guide: str,
        small_only: bool,
        item_id: str = "",
        whole: str = "",
    ) -> list[dict]:
        said = said_about(self.data_dir / "transcript.jsonl", item_id, section)
        prompt = build_prompt(
            section, title, self.bank.for_section(section), said, small_only, whole
        )
        system = guide + "\n\nAnswer with the JSON object only."
        argv = claude_p._completion_argv(prompt, system=system, model=EDIT_MODEL)
        cut = argv.index("--")
        argv[cut:cut] = ["--effort", EDIT_EFFORT, "--json-schema", json.dumps(SCHEMA)]
        with tempfile.TemporaryDirectory(prefix="voice_edits_") as cwd:
            result = claude_p._run_blocking(
                argv, env=claude_p._child_env(False), cwd=cwd
            )
        text = result.text.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        edits = json.loads(text).get("edits", [])
        out = []
        for e in edits:
            find = e.get("find", "")
            if not find or find not in section:
                continue
            # Small fixes: each one changes a stretch inside `find`, and they can be combined.
            small, seen = [], set()
            for fix in e.get("small", []):
                old, new = fix.get("from", ""), fix.get("to", "")
                if not old or old == new or old not in find or old in seen:
                    continue
                seen.add(old)
                small.append({"from": old, "to": new})
            rewrites = [
                r
                for r in e.get("rewrites", [])
                if isinstance(r, str) and r.strip() and r != find
            ][:3]
            all_small = find
            for fix in small:
                all_small = all_small.replace(fix["from"], fix["to"], 1)
            # `options` keeps what accepting without the card would put in (all small fixes, then rewrites).
            options = ([all_small] if small else []) + rewrites
            out.append(
                {
                    "kind": e.get("kind", ""),
                    "find": find,
                    "small": small,
                    "rewrites": rewrites,
                    "options": options,
                    "replace": options[0] if options else "",
                    "why": e.get("why", ""),
                    "quote": e.get("quote", ""),
                    "source_title": e.get("source_title", ""),
                }
            )
        return out
