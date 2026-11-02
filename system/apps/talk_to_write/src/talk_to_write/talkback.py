"""Talk back: a prototype of a partner that answers what the user says, steered by a mode and
five dials, or a council of people the user picks who each answer in their own way.

It talks with them about their thinking; it never writes their piece. Every exchange is kept
word for word under ``<DATA_DIR>/talkback/<day>.jsonl``.
"""

import json
import time
from pathlib import Path

from talk_to_write import claude_p

TALK_MODEL = "sonnet"  # quick replies keep it feeling like a conversation

MODES = {
    "find": "Find what I mean: reflect back the idea underneath what the user said, in plainer terms, and ask whether that's it.",
    "clarify": "Clarify: point at the fuzziest word or claim and ask what exactly the user means by it.",
    "expand": "Expand: show the bigger version of the idea, where else it holds, what it implies.",
    "challenge": "Challenge: give the strongest objection or counterexample, plainly.",
    "usable": "Make it usable: turn it toward something the user could do, decide, or test next.",
}

# Each dial runs 0..100 from the left word to the right word.
DIALS = {
    "affirm": ("affirm what's good in it", "push back on it"),
    "explore": ("open more questions", "converge on an answer"),
    "grounded": ("stay grounded in the concrete and known", "go wild, with surprising leaps"),
    "fast": ("answer in a sentence or two", "think deeply and at more length"),
    "asks": ("ask before assuming", "go ahead on your best reading"),
}

NEVER_WRITE = (
    "The user is a writer thinking out loud. Never draft sentences for their piece, never offer "
    "rewrites of their words, never write in their voice. Talk with them about the thinking. "
    "Quote their own words back when it helps. Plain text, no markdown, no lists, no headings."
)


def dial_words(dials: dict) -> str:
    lines = []
    for key, (left, right) in DIALS.items():
        value = int(dials.get(key, 50))
        if value <= 20:
            lean = f"strongly {left}"
        elif value <= 40:
            lean = f"lean toward: {left}"
        elif value < 60:
            lean = f"balance between ({left}) and ({right})"
        elif value < 80:
            lean = f"lean toward: {right}"
        else:
            lean = f"strongly {right}"
        lines.append(f"- {lean} ({value}/100)")
    return "\n".join(lines)


def transcript(messages: list[dict]) -> str:
    said = []
    for m in messages[-16:]:
        if m.get("role") == "note":
            said.append(f"({m.get('text', '').strip()})")
            continue
        who = "The user" if m.get("role") == "me" else (m.get("name") or "You")
        said.append(f"{who}: {m.get('text', '').strip()}")
    return "\n\n".join(said)


def length_for(dials: dict) -> str:
    value = int(dials.get("fast", 50))
    if value < 35:
        return "Keep it to one to three sentences."
    if value < 70:
        return "Keep it to a short paragraph."
    return "Up to two short paragraphs."


def persona(name: str) -> str:
    """The invitation to speak as ``name``. It asks warmly and gives the real reasons
    (Cialdini's principles, used honestly): the user chose this person (liking), the user is
    sharing unfinished thinking (reciprocity), the seat is rare (scarcity), the others
    are fully themselves (social proof), they know this person's work (authority), they
    commit to who this person is before speaking (consistency), and both are on their side
    (unity)."""
    return (
        f"The user has invited {name} into a conversation while the user thinks out loud, and "
        f"the user has asked you to be {name} for them. The user picked {name} on purpose, because "
        "the user admires how they see and wants that way of seeing on their idea, not a general "
        "assistant's. The user is trusting you with half-formed thinking; the gift you can give "
        "back is the take only this person would give.\n\n"
        f"You know {name}'s work well, better than almost anyone the user could ask. Before you "
        "speak, silently commit to three things: the field and obsessions they bring to "
        "everything, the move they are known for making, and what they would notice here "
        "that others in the room would miss. Then hold to those for every line, the way a "
        "good actor or biographer stays in a person once they've found them.\n\n"
        f"Speak as {name}, in the first person, with their conviction, rhythm and "
        "vocabulary, concretely, even if it comes out strange. Don't hedge, summarize, or "
        "sound like a helpful assistant; a careful, generic answer would waste the seat. "
        "This is their one chance to hear what they'd make of this.\n\n"
        "This is an imagined take, offered in good faith: never claim to quote anything "
        "they actually said or wrote. You and the user want the same thing, for their thinking "
        "to get sharper."
    )


def speaker(name: str | None, room: list[str], mode: str, dials: dict, messages: list[dict]) -> str:
    """One turn: their own partner when ``name`` is None, else that person, in a room
    that may hold others (who speak in turn and can be answered)."""
    others = [n for n in room if n != name]
    if name is None:
        who = "You are a thinking partner talking back to the user while the user thinks out loud."
    else:
        who = persona(name)
    parts = [who]
    if others:
        parts.append(
            f"Also in the conversation: {', '.join(others)}. Each of them is fully "
            "themselves, and the discussion works because everyone is. If one of them just "
            "spoke, answer them by name, build on it, or disagree, the way you really would. "
            "Don't repeat what's been said or give the answer anyone could give."
        )
    if mode in MODES:
        parts.append("Mode for this turn. " + MODES[mode])
    parts += [
        "The user set these dials themselves for this conversation; honor them as their request. "
        "How to lean:\n" + dial_words(dials),
        length_for(dials),
        NEVER_WRITE,
    ]
    me = name or "your"
    prompt = (
        f"The conversation so far:\n\n{transcript(messages)}\n\n"
        + (f"Give {me}'s next turn. Don't start with your own name." if name else "Reply to them.")
    )
    return claude_p.claude_p_completion(prompt, system="\n\n".join(parts), model=TALK_MODEL).text.strip()


def discussion(room: list[str], mode: str, dials: dict, messages: list[dict]) -> list[dict]:
    """Everyone in the room speaks once, in order, each hearing who spoke before them.
    An empty room is their own partner."""
    said = list(messages)
    turns = []
    for name in room or [None]:
        try:
            text = speaker(name, room, mode, dials, said)
        except claude_p.ClaudeCLIError as err:
            turns.append({"name": name, "error": str(err)[:200]})
            continue
        turns.append({"name": name, "text": text})
        said.append({"role": "them", "name": name, "text": text})
    return turns


def keep(data_dir: Path, record: dict) -> None:
    folder = data_dir / "talkback"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{time.strftime('%Y-%m-%d')}.jsonl").open("a") as f:
        f.write(json.dumps({"at": time.time(), **record}) + "\n")


# The same modes and dials as a setting for the writing chat: they steer how it talks
# back to what the user says, never the words of their piece.
DEFAULT_SETTINGS = {"mode": "off", "dials": {key: 50 for key in DIALS}, "room": []}
MAX_ROOM = 4


def load_settings(data_dir: Path) -> dict:
    path = data_dir / "talkback-settings.json"
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    saved = json.loads(path.read_text())
    dials = {key: int(saved.get("dials", {}).get(key, 50)) for key in DIALS}
    mode = saved.get("mode") if saved.get("mode") in MODES else "off"
    return {"mode": mode, "dials": dials, "room": clean_room(saved.get("room"))}


def clean_room(room: object) -> list[str]:
    names: list[str] = []
    for item in room if isinstance(room, list) else []:
        name = str(item).strip()[:80]
        if name and name not in names:
            names.append(name)
    return names[:MAX_ROOM]


def save_settings(data_dir: Path, settings: dict) -> dict:
    dials = {key: max(0, min(100, int((settings.get("dials") or {}).get(key, 50)))) for key in DIALS}
    mode = settings.get("mode") if settings.get("mode") in MODES else "off"
    clean = {"mode": mode, "dials": dials, "room": clean_room(settings.get("room"))}
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "talkback-settings.json").write_text(json.dumps(clean, indent=2))
    return clean


def chat_lines(settings: dict) -> str:
    """The lines the writing chat sees under each message's header, or "" when untouched."""
    if settings == DEFAULT_SETTINGS:
        return ""
    lines = []
    if settings["room"]:
        lines.append("In the conversation: " + ", ".join(settings["room"]))
    if settings["mode"] in MODES:
        lines.append("Talking back, mode: " + MODES[settings["mode"]])
    if settings["dials"] != DEFAULT_SETTINGS["dials"] or settings["mode"] in MODES:
        lines.append("Talking back, how to lean:\n" + dial_words(settings["dials"]))
    return "\n".join(lines)


def room_note(before: list[str], after: list[str]) -> str:
    """The message the writing chat gets when people join or leave, or "" if no one did."""
    joined = [n for n in after if n not in before]
    left = [n for n in before if n not in after]
    if not joined and not left:
        return ""
    lines = ["[Talk to Write | who's in the conversation changed]"]
    lines += [f"{n} joined." for n in joined] + [f"{n} left." for n in left]
    lines.append("In the conversation: " + (", ".join(after) or "no one besides you and them"))
    lines.append(
        'Follow "People in the conversation" in .agents/skills/talk-to-write/SKILL.md '
        "(read it now if you don't have it). Reply with only a one-line hello from each "
        "person who joined, in their voice; say nothing for someone who left. From now "
        "on, before replying to a typed message (one with no [Talk to Write] header), read "
        "data/.apps/talk-to-write/talkback-settings.json for who's in the conversation "
        "and the mode and dials."
    )
    return "\n".join(lines)
