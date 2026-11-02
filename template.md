---
title: "Talk to Write"
description: "Talk out loud and turn the best of what you say into essays in your own voice"
thumbnail: "template.svg"
version: v1
format: v2
---

# Talk to Write

This file is the manifest for the **Talk to Write** template (slug:
`talk-to-write`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Talk out loud and turn the best of what you say into essays in your own voice

Talk to Write is a writing app for people who think best out loud. You talk (from
your phone or your desktop), and a dedicated writing chat turns what you said into
proposed changes to your draft, built only from your own spoken words, cleaned up
(filler and false starts dropped, grammar fixed), never sentences the AI made up.
Every change shows up in the document as a blue suggestion you accept, skip, or
rework, so the words stay yours. The app has three tabs: **Talk** (the writing chat,
whose replies are read aloud), **Canvas** (your documents, plus HTML pages the chat
makes for you, such as a narrative map or a chart), and **Garden** (an Obsidian-style
notes folder you connect, read and edited in place). A "Suggest edits" button asks
for line edits in your own written voice: each one comes with small fixes you can
combine and fuller rewrites, and, once you point it at samples of your writing, the
line of yours it is modelled on. Each piece keeps a running history of everything you
said about it, so the chat picks up where you left off.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/talk_to_write`
- `system/supervisord.conf.d/talk-to-write.conf`
- `.agents/skills/talk-to-write`
- `.agents/skills/suggest-voice-edits`

- `system/apps/talk_to_write` -- the app: a Flask backend (`src/talk_to_write/runner.py`)
  and a single-page frontend (`static/`), with a prebuilt rich-text editor bundle
  (`static/editor.js`, rebuilt from `editor/` with `editor/build.sh` only if you change
  it). Modules: `store.py` (Canvas documents and pages, with Trash), `garden.py` (the
  notes folder, edited in place; deletes go to the folder's `.trash`), `versions.py`
  (version history per document), `threads.py` (one running history per piece),
  `chat_bridge.py` (the writing chat), `editor.py` (voice edits), `speaking.py` (a
  record of everything spoken, and a "speaking voice" section in the voice guide),
  `talkback.py` (the talk-back modes, dials and invited "council" voices), and
  `claude_p.py` (the helper for calling Claude).
- `system/supervisord.conf.d/talk-to-write.conf` -- the supervisord program
  `talk-to-write`, which registers the app on port **8099** through
  `forward_port.py` (from `app.toml`) and runs the `talk-to-write` console script.
  `app.toml` keeps it running with no window open, since it is often used from a phone.
- `.agents/skills/talk-to-write` -- the skill the writing chat loads as its first
  message (`/talk-to-write`). It defines how the chat reads each message's header
  (which screen you are on, what you are pointing at), how it turns speech into
  proposed changes (blend into existing text; new ideas as your own cleaned-up
  sentences), where documents and notes live, and the app's settings.
- `.agents/skills/suggest-voice-edits` -- the voice-edit skill: `editing-guide.md`
  (what makes a good edit, shared with the app's own edit pass),
  `scripts/build_voice_bank.py` (builds `data/.apps/talk-to-write/voice/passages.jsonl`
  from the folders of your writing you list in settings) and `scripts/find_passages.py`
  (searches it).

At runtime: the page talks to the Flask app on 8099. Speech recognition and reading
replies aloud happen in the browser (the Web Speech API). What you say is posted to
the app, which forwards it, with a header describing your screen, to one chat it
creates in the workspace's chat app (loopback, `http://127.0.0.1:8010`), and follows
that chat's replies so the page can speak them. The chat posts proposed changes back
to the app's `/api/proposals` endpoint. Voice edits, the speaking-voice summary and
the talk-back voices call Claude directly through headless `claude -p`. All data
lives under `data/.apps/talk-to-write/` (documents, media, transcript, histories,
versions, voice bank, and `settings.json`); the Garden edits the connected notes
folder directly.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.


- requires_llm: calls Claude keyless through headless `claude -p` on the workspace's Claude subscription (voice edits use Opus at high effort; the writing chat runs in the workspace chat app on Opus); an adopter with only an API key must switch `claude_p.py` calls to the keyed litellm path per use-ai-integration

Adaptation:

- **No notes folder is connected.** The Garden tab shows "Connect a notes folder"
  until one is set. Ask the user whether they keep notes in an Obsidian-style folder
  (synced into the workspace, for example); if so, set `notes_folder` in
  `data/.apps/talk-to-write/settings.json` (or the `TALK_TO_WRITE_NOTES_FOLDER`
  environment variable) and restart the app. If not, the Garden tab can stay unused.
- **No voice samples are configured.** Voice edits run, but without passages of the
  user's own writing they are much weaker and carry no "based on your line" quote.
  Ask the user where their finished writing lives (essays, a blog's markdown, a notes
  folder), add those folders to `voice_sources` in the settings file with a `kind`
  each, and run `scripts/build_voice_bank.py`.
- **The "who to invite" suggestions** in the talk-back panel (well-known public
  figures) are the publisher's picks; offer to change them to people the user admires
  (`ROOM_IDEAS` in `static/app.js` and `IDEAS` in `static/talkback.html`).

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `connect-external-service` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-10-29) -- first version: the Talk to Write app, its writing-chat skill and the voice-edit skill, with the notes folder and voice samples left for the adopter to connect

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
