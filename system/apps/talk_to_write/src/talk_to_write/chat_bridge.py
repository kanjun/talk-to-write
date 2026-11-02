"""The writing chat: one ongoing chat in the workspace's chat app, made for this app.

The chat app (loopback, no auth) does the agent work: model, memory, condensing.
This module creates the chat once (Opus 5.5 at medium effort on their Claude
subscription, started on the talk-to-write skill), sends it what the user says with
where the user is, and follows its replies so the page can read them aloud.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

CHAT_URL = "http://127.0.0.1:8010"
CHAT_NAME = "Talk to Write"
FIRST_MESSAGE = "/talk-to-write"
NOT_THE_USER = ("[", "/", "<", "Base directory for this skill", "You are continuing the chat", "Stop hook feedback", "Context was compacted")
MODEL = {"model_id": "opus[1m]", "effort": "medium", "fast": False}

log = logging.getLogger(__name__)


class ChatError(Exception):
    """Something the user should be told about in plain words."""


def _request(
    method: str, path: str, body: dict | None = None, timeout: float = 30
) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        CHAT_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:300]
        raise ChatError(f"The chat app said no ({err.code}): {detail}") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise ChatError("The chat app isn't answering right now.") from err
    return json.loads(raw) if raw else {}


def claude_subscription_account() -> str:
    accounts = _request("GET", "/api/accounts").get("accounts", [])
    for acct in accounts:
        if (
            acct.get("harness") == "claude"
            and acct.get("reauth_method") == "subscription"
        ):
            return acct["id"]
    for acct in accounts:
        if acct.get("harness") == "claude":
            return acct["id"]
    raise ChatError("There's no Claude account connected to the chat app.")


def chat_exists(chat_id: str) -> bool:
    try:
        _request("GET", f"/api/chats/{chat_id}/events?limit=1", timeout=10)
        return True
    except ChatError as err:
        # Only a "no such chat" means it's gone; if the chat app is just down, keep the one we have.
        return "(404)" not in str(err)


class ChatBridge:
    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str], None]] = []
        self._typed: list[Callable[[str], None]] = []
        self._follower: threading.Thread | None = None

    # -- the chat's identity ----------------------------------------------------

    def _load(self) -> dict:
        return (
            json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        )

    def _save(self, data: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(data, indent=1))

    def chat_id(self) -> str:
        """The writing chat, made the first time it's needed (and again if it was deleted)."""
        with self._lock:
            data = self._load()
            existing = data.get("chat_id")
            if existing and chat_exists(existing):
                return existing
            made = _request(
                "POST",
                "/api/chats/create",
                {
                    "name": CHAT_NAME,
                    "account_id": claude_subscription_account(),
                    "message": FIRST_MESSAGE,
                    "model": MODEL,
                    "labels": {"talk_to_write": "1"},
                    "should_wait": True,
                },
                timeout=240,
            )
            data["chat_id"] = made["chat_id"]
            data["created"] = time.time()
            self._save(data)
            return made["chat_id"]

    def known_chat_id(self) -> str | None:
        return self._load().get("chat_id")

    # -- talking to it --------------------------------------------------------

    def send(self, message: str) -> None:
        _request("POST", f"/api/chats/{self.chat_id()}/message", {"message": message})

    # -- following its replies -------------------------------------------------

    def on_reply(self, listener: Callable[[str], None]) -> None:
        self._listeners.append(listener)
        if self._follower is None:
            self._follower = threading.Thread(
                target=self._follow, daemon=True, name="chat-replies"
            )
            self._follower.start()

    def on_typed(self, listener: Callable[[str], None]) -> None:
        """Hear what the user types straight into the chat (messages without the app's header)."""
        self._typed.append(listener)

    def _follow(self) -> None:
        seen: set[str] = set()
        while True:
            chat_id = self.known_chat_id()
            if not chat_id:
                time.sleep(2)
                continue
            try:
                # Everything already said is history: only new replies are read aloud.
                history = _request(
                    "GET", f"/api/chats/{chat_id}/events?limit=500", timeout=20
                )
                seen.update(e.get("event_id", "") for e in history.get("events", []))
                req = urllib.request.Request(f"{CHAT_URL}/api/chats/{chat_id}/stream")
                with urllib.request.urlopen(req, timeout=120) as stream:
                    for raw in stream:
                        line = raw.decode(errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        self._handle(event, seen)
                        if self.known_chat_id() != chat_id:
                            break
            except (
                Exception
            ) as err:  # the stream drops when the chat app restarts; reconnect
                log.info("reply stream ended: %s", err)
                time.sleep(3)

    def _handle(self, event: dict, seen: set[str]) -> None:
        event_id = event.get("event_id", "")
        if event_id in seen:
            return
        if event.get("type") == "user_message":
            seen.add(event_id)
            said = (event.get("content") or "").strip()
            # Skip the app's own messages ("[Talk to Write ..."), skill loads ("/..."), and
            # what the chat app itself injects (command output, handoffs, compaction notes).
            if said and not said.startswith(NOT_THE_USER):
                for listener in list(self._typed):
                    listener(said)
            return
        if event.get("type") != "assistant_message":
            return
        seen.add(event_id)
        text = (event.get("text") or "").strip()
        if not text or event.get("is_api_error") or event.get("is_auth_error"):
            return
        for listener in list(self._listeners):
            listener(text)
