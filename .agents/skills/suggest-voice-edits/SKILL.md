---
name: suggest-voice-edits
description: Suggest line edits to a draft of the user's, in their own written voice, each backed by a real passage the user wrote (when voice samples are set up). Use from the Talk to Write chat when the user asks for edits (whole document or just the part the user is pointing at or names), or when the user asks anywhere for edits "in my voice" on a document in the Talk to Write app.
---

# Suggest edits in their voice

The Talk to Write app does the work itself: one fast call per section of the
document, run in parallel. Style comes from their own raw writing (all their finished
essays and site pages, plus the garden notes closest to that section, from the voice
sources they set up; no summary of their style), and each call sees the whole piece so its edits serve what the section
is doing in it. Only edits that clearly beat their draft come back, so a strong section
may get none. What the user has said out loud to the app about
the piece is given too, but only for meaning: where the draft misses an idea the user
clearly meant. Edits never move the wording toward their speech. Each edit offers small
fixes the user can combine and fuller rewrites, and the user can change any of them before
accepting. The "Suggest edits" button in the app
calls the same thing directly, without the chat.

What makes a good edit is defined once, in
[`editing-guide.md`](editing-guide.md) (most valuable first: AI-sounding lines,
abstract-where-they'd-be-concrete, weak landings, rhythm, then trims). Read it if
the user asks how edits are chosen or wants that changed; change it there, not here.

## Asking for edits (from the chat)

Whole document, or one part of it (`scope` is the exact passage text, e.g. the
"in the passage" text from the message header, or a section you found in the file):

```bash
curl -s -X POST http://127.0.0.1:8099/api/suggest -H 'Content-Type: application/json' \
  -d '{"side": "canvas", "kind": "doc", "id": "the-quiet-hour"}'
curl -s -X POST http://127.0.0.1:8099/api/suggest -H 'Content-Type: application/json' \
  -d '{"side": "canvas", "kind": "doc", "id": "the-quiet-hour", "scope": "<exact passage text>"}'
```

For a garden note: `"side": "k", "kind": "note", "id": "<note path without .md>"`.
Add `"small": true` when the user asks for small edits only (every option then keeps
nearly all their words). It returns at once (`{"started": true, "sections": N}`); the edits show up in the
document over the next couple of minutes. Tell them in one spoken line that they're on
their way. Don't wait for them, and don't post edits yourself.

## Their voice, for anything else

- Voice samples come from the folders listed under `voice_sources` in
  `data/.apps/talk-to-write/settings.json` (shape and kinds in the `talk-to-write`
  skill's "Settings" section). There is no default: with none set, the bank is empty
  and edits run without passages to match or quote, which makes them noticeably
  weaker. When the user wants better edits, offer to set up their sources.
- Style guide: `data/.apps/talk-to-write/voice/profile.md` (its speaking-voice
  section is kept up to date by the app).
- Their passages: `data/.apps/talk-to-write/voice/passages.jsonl`, searchable with
  `uv run python .agents/skills/suggest-voice-edits/scripts/find_passages.py <words> [--kind essay]`.
  Prefer finished prose (`essay`, `page`, `garden`). Rough notes may hold private
  material (journals, health, relationships): never quote those to them alongside a draft.
- Build or rebuild the bank after changing the sources, or when their writing has grown:
  `uv run python .agents/skills/suggest-voice-edits/scripts/build_voice_bank.py`
  (reads only their own words).
