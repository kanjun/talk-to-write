"""The user's speaking voice: everything the user says to the app, kept word for word, and what it shows.

- ``voice/spoken.md`` is the readable record of every message the user has spoken into the
  app (when, where the user was, their exact words). It grows with each message.
- The voice guide (``voice/profile.md``) gets a "speaking voice" section, rewritten from
  that record every time enough new speech has come in. The section sits between two
  marker lines so the rest of the guide is never touched.

Both feed the writing chat and the voice edits: the edits use the guide in every
prompt, so their speaking voice tells them what to keep and what to edit away.
"""

import json
import logging
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from talk_to_write import claude_p

SUMMARY_MODEL = "opus"
SUMMARY_EFFORT = "medium"
FIRST_SUMMARY_WORDS = 300  # write the section once there's this much speech
REFRESH_EVERY_WORDS = 1500  # then rewrite it after this much more
MAX_WORDS_SHOWN = 40000  # the most recent speech the summary reads
TIMEZONE_FILE = Path("data/.state/user_timezone")
START = "<!-- speaking-voice:start -->"
END = "<!-- speaking-voice:end -->"

PROMPT = """Below is everything the user has said out loud to their writing app, word for word (phone dictation, so punctuation and some words may be mis-heard). Write the "speaking voice" section of their voice guide: how the user talks when the user is thinking out loud about their ideas.

Cover, with short verbatim quotes from the record for every claim:
- how the user builds an idea out loud: openings, false starts, the phrases the user reaches for, how the user gets to the point;
- their recurring words, images, and turns of phrase;
- rhythm: sentence length, repetition, asides, how the user emphasizes;
- what's strong and worth keeping when their words become a draft (the lines that already sound finished);
- what usually needs editing away when speech becomes their written voice (filler, hedges, restarts), compared with how the user writes (the existing guide's written-voice sections are given for comparison).

Plain, specific, useful to an editor choosing their best lines. 300-700 words. Start with the heading "## Speaking voice". No preamble.

# Their written voice guide (for comparison)

{profile}

# Everything the user has said to the app

{spoken}
"""

log = logging.getLogger(__name__)


def stamp(at_iso: str) -> str:
    """When the user said it, in their own time zone."""
    try:
        when = datetime.strptime(at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return at_iso
    try:
        zone = ZoneInfo(TIMEZONE_FILE.read_text().strip())
    except (OSError, ValueError, ZoneInfoNotFoundError):
        return when.strftime("%b %d, %Y, %H:%M UTC")
    return when.astimezone(zone).strftime("%b %-d, %Y, %-I:%M %p")


def where(context: dict) -> str:
    item = context.get("item") or {}
    if item.get("title"):
        return f' — in "{item["title"]}"'
    side = context.get("side")
    return " — in the Garden" if side == "garden" else " — on the Canvas"


class SpeakingVoice:
    def __init__(self, voice_dir: Path) -> None:
        self.dir = voice_dir
        self.log_file = voice_dir / "spoken.md"
        self.state_file = voice_dir / "speaking_state.json"
        self.profile = voice_dir / "profile.md"
        self._lock = threading.Lock()
        self._refreshing = False

    def _state(self) -> dict:
        return (
            json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        )

    def _save_state(self, state: dict) -> None:
        self.state_file.write_text(json.dumps(state, indent=1))

    def add(self, entry: dict) -> None:
        """Keep one spoken message, then rewrite the guide's section if enough is new."""
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            fresh = not self.log_file.exists()
            with self.log_file.open("a") as handle:
                if fresh:
                    handle.write(
                        "# Everything I've said to Talk to Write\n\n"
                        "Word for word, as dictated. Part of my voice guide: it's how I sound when I talk.\n"
                    )
                handle.write(
                    f"\n### {stamp(entry['at'])}{where(entry.get('context') or {})}\n\n{entry['text'].strip()}\n"
                )
            state = self._state()
            state["words"] = state.get("words", 0) + len(entry["text"].split())
            self._save_state(state)
        self.maybe_refresh()

    def backfill(self, transcript: list[dict]) -> None:
        """Start the record from what the user has already said, the first time."""
        if self.log_file.exists():
            return
        for entry in transcript:
            if entry.get("how") == "spoken":
                self.add(entry)

    def maybe_refresh(self) -> None:
        state = self._state()
        words, at_last = state.get("words", 0), state.get("summarized_words", 0)
        due = (at_last == 0 and words >= FIRST_SUMMARY_WORDS) or (
            at_last and words - at_last >= REFRESH_EVERY_WORDS
        )
        if due and not self._refreshing:
            self._refreshing = True
            threading.Thread(
                target=self._refresh, args=(words,), daemon=True, name="speaking-voice"
            ).start()

    def _refresh(self, words: int) -> None:
        try:
            spoken = self.log_file.read_text()
            spoken_words = spoken.split()
            if len(spoken_words) > MAX_WORDS_SHOWN:
                spoken = "(earlier speech left out)\n" + " ".join(
                    spoken_words[-MAX_WORDS_SHOWN:]
                )
            profile = (
                self.profile.read_text() if self.profile.exists() else "(none yet)"
            )
            written = profile.split(START)[0]
            prompt = PROMPT.format(profile=written, spoken=spoken)
            argv = claude_p._completion_argv(
                prompt,
                system="You write precise style guides for an editor.",
                model=SUMMARY_MODEL,
            )
            cut = argv.index("--")
            argv[cut:cut] = ["--effort", SUMMARY_EFFORT]
            with tempfile.TemporaryDirectory(prefix="speaking_voice_") as cwd:
                result = claude_p._run_blocking(
                    argv, env=claude_p._child_env(False), cwd=cwd
                )
            section = result.text.strip()
            if not section.startswith("## "):
                raise ValueError("unexpected summary shape")
            note = f"*Rewritten {time.strftime('%Y-%m-%d')} from {words:,} spoken words in `spoken.md`.*"
            block = f"{START}\n{section}\n\n{note}\n{END}"
            with self._lock:
                current = self.profile.read_text() if self.profile.exists() else ""
                if START in current and END in current:
                    head, rest = current.split(START, 1)
                    tail = rest.split(END, 1)[1]
                    updated = head + block + tail
                else:
                    updated = current.rstrip() + "\n\n" + block + "\n"
                self.profile.write_text(updated)
                state = self._state()
                state["summarized_words"] = words
                state["summarized_at"] = time.time()
                self._save_state(state)
        except Exception:
            log.exception("couldn't rewrite the speaking voice section")
        finally:
            self._refreshing = False
