import os
import tempfile

# Everything the app writes while these run goes to a throwaway folder, never their data.
os.environ["TALK_TO_WRITE_DATA_DIR"] = tempfile.mkdtemp(prefix="ttw-test-", dir="/var/tmp")

from talk_to_write import runner  # noqa: E402

ESSAY = '---\ntitle: "The quiet hour"\n---\n\nFirst paragraph.\n\nThe streets are empty. Even the birds sound careful.\n\nLast paragraph.\n'


def _say(monkeypatch, context):
    sent = []
    monkeypatch.setattr(runner.chat, "send", sent.append)
    monkeypatch.setattr(runner, "read_item", lambda side, kind, item_id: (ESSAY, 0.0))
    reply = runner.app.test_client().post("/api/say", json={"text": "I don't love this.", "context": context})
    assert reply.status_code == 200
    return sent[0]


def test_the_chat_is_asked_to_read_the_whole_essay_without_it_filling_the_chat(monkeypatch):
    message = _say(
        monkeypatch,
        {
            "side": "canvas",
            "view": "doc",
            "item": {"kind": "doc", "id": "the-quiet-hour", "title": "The quiet hour"},
            "pointing": {"what": "text", "text": "birds sound careful", "in_passage": "The streets are empty. Even the birds sound careful."},
        },
    )
    assert "Before replying, read the whole document as it is now" in message
    assert "the-quiet-hour.md" in message and 'Pointing at: "birds sound careful"' in message
    assert "First paragraph." not in message and "Last paragraph." not in message
    assert message.endswith("\n\nI don't love this.")


def test_no_document_is_sent_from_a_list(monkeypatch):
    message = _say(monkeypatch, {"side": "canvas", "view": "list"})
    assert "read the whole document" not in message
    assert message.endswith("\n\nI don't love this.")


def _record(at, item_id, text, how="spoken", side="canvas"):
    return {"at": at, "how": how, "text": text, "context": {"side": side, "view": "doc", "item": {"kind": "doc", "id": item_id, "title": item_id.title()}}}


def test_coming_back_to_an_essay_brings_its_whole_history(monkeypatch, tmp_path):
    from talk_to_write import threads

    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    (tmp_path / "canvas").mkdir()
    for name in ("essay.md", "essay-narrative-map.html", "other.md"):
        (tmp_path / "canvas" / name).write_text("x")
    records = [
        _record("2026-10-27T20:00:00Z", "essay-narrative-map", "Map: end the first section on the walk home.", how="typed"),
        _record("2026-10-28T10:00:00Z", "essay", "Stillness, not silence."),
        _record("2026-10-28T11:00:00Z", "other", "Rain changes the route."),
        _record("2026-10-28T12:00:00Z", "essay", "Now this line."),
    ]
    monkeypatch.setattr(runner.store, "transcript", lambda: records)
    past = runner.history_on_switch(records[-1]["context"])
    assert past.startswith("The user has just come back to this piece") and past.rstrip().count("\n") == 1
    assert str((tmp_path / "threads" / "essay.md").resolve()) in past
    assert "canvas/essay-narrative-map" in past and "Stillness" not in past
    thread = (tmp_path / "threads" / "essay.md").read_text()
    assert "Map: end the first section on the walk home." in thread and "Stillness, not silence." in thread
    assert thread.index("Map:") < thread.index("Stillness") and "Rain" not in thread

    # Still on the same piece (the map counts): nothing to reload.
    records.append(_record("2026-10-28T12:05:00Z", "essay-narrative-map", "And here."))
    assert runner.history_on_switch(records[-1]["context"]) == ""

    # Linked documents share one history.
    threads.link(tmp_path, ["canvas/other", "k/Notes on mornings"])
    records.append(_record("2026-10-28T13:00:00Z", "Notes on mornings", "Care.", side="garden"))
    past = runner.history_on_switch(records[-1]["context"])
    assert "other.md" in past
    thread = (tmp_path / "threads" / "other.md").read_text()
    assert "Rain changes the route." in thread and "Care." in thread and "Stillness" not in thread


def test_what_she_types_is_kept_with_where_she_was(monkeypatch, tmp_path):
    import json

    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    kept = []
    monkeypatch.setattr(runner.store, "record", lambda text, context, how: kept.append((text, context, how)))
    monkeypatch.setattr(runner, "refresh_threads", lambda context: [])
    context = {"side": "canvas", "view": "doc", "item": {"kind": "doc", "id": "essay", "title": "Essay"}}
    (tmp_path / "screen.json").write_text(json.dumps({"context": context}))
    runner.record_typed("I meant ickiness.")
    assert kept == [("I meant ickiness.", context, "typed")]


def test_only_her_own_typing_counts():
    from talk_to_write.chat_bridge import ChatBridge

    bridge = ChatBridge(runner.DATA_DIR / "chat-test.json")
    heard = []
    bridge.on_typed(heard.append)
    for i, said in enumerate(["[Talk to Write | spoken]\nhi", "/talk-to-write", "<local-command-stdout>x", "Context was compacted", "I meant ickiness."]):
        bridge._handle({"type": "user_message", "event_id": str(i), "content": said}, set())
    assert heard == ["I meant ickiness."]
