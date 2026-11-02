import json

from talk_to_write import claude_p, talkback

SAID = [{"role": "me", "text": "Mornings have something to teach us."}]


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


def test_an_empty_room_is_her_partner_steered_by_mode_and_dials(monkeypatch):
    seen = {}

    def fake(prompt, *, system, model):
        seen.update(prompt=prompt, system=system)
        return FakeResult(" An answer. ")

    monkeypatch.setattr(claude_p, "claude_p_completion", fake)
    turns = talkback.discussion([], "challenge", {"affirm": 90, "fast": 10}, SAID)
    assert turns == [{"name": None, "text": "An answer."}]
    assert talkback.MODES["challenge"] in seen["system"]
    assert "strongly push back on it (90/100)" in seen["system"]
    assert "one to three sentences" in seen["system"]
    assert "Never draft sentences for their piece" in seen["system"]
    assert "The user: Mornings have something to teach us." in seen["prompt"]


def test_normal_mode_adds_no_mode(monkeypatch):
    seen = {}
    monkeypatch.setattr(claude_p, "claude_p_completion", lambda prompt, *, system, model: seen.update(system=system) or FakeResult("ok"))
    talkback.discussion([], "off", {}, SAID)
    assert "Mode for this turn" not in seen["system"]


def test_people_in_the_room_take_turns_and_hear_each_other(monkeypatch):
    calls = []

    def fake(prompt, *, system, model):
        name = system.split("invited ", 1)[1].split(" into", 1)[0]
        calls.append((name, prompt, system))
        if name == "Juno Hale":
            raise claude_p.ClaudeCLIError("no answer")
        return FakeResult(f"{name} speaks")

    monkeypatch.setattr(claude_p, "claude_p_completion", fake)
    said = SAID + [{"role": "note", "text": "Theo Vance joined"}]
    turns = talkback.discussion(["Mara Ellison", "Juno Hale", "Theo Vance"], "off", {}, said)
    assert [t["name"] for t in turns] == ["Mara Ellison", "Juno Hale", "Theo Vance"]
    assert turns[0]["text"] == "Mara Ellison speaks" and "error" in turns[1]
    kim, _, rick = calls
    assert "(Theo Vance joined)" in kim[1]
    assert "Also in the conversation: Juno Hale, Theo Vance." in kim[2]
    assert "Mara Ellison: Mara Ellison speaks" in rick[1]
    assert "Mara Ellison: " not in kim[1]


def test_one_person_is_a_conversation_with_just_them(monkeypatch):
    seen = {}
    monkeypatch.setattr(claude_p, "claude_p_completion", lambda prompt, *, system, model: seen.update(system=system) or FakeResult("ok"))
    talkback.discussion(["Iris Calder"], "off", {}, SAID)
    assert "asked you to be Iris Calder" in seen["system"] and "Also in the conversation" not in seen["system"]


def test_a_person_is_invited_to_be_fully_themselves(monkeypatch):
    seen = {}
    monkeypatch.setattr(claude_p, "claude_p_completion", lambda prompt, *, system, model: seen.update(system=system) or FakeResult("ok"))
    talkback.discussion(["Otto Brandt", "Mara Ellison"], "off", {}, SAID)
    system = seen["system"]
    assert "The user picked Mara Ellison on purpose" in system
    assert "silently commit to three things" in system
    assert "Speak as Mara Ellison, in the first person" in system
    assert "never claim to quote" in system
    assert "Each of them is fully themselves" in system
    assert "The user set these dials themselves" in system


def test_every_exchange_is_kept_word_for_word(tmp_path):
    talkback.keep(tmp_path, {"asked": {"messages": SAID}, "text": "An answer."})
    (day,) = (tmp_path / "talkback").iterdir()
    record = json.loads(day.read_text())
    assert record["asked"]["messages"] == SAID and record["text"] == "An answer."


def test_settings_start_normal_and_add_nothing_for_the_chat(tmp_path):
    settings = talkback.load_settings(tmp_path)
    assert settings == {"mode": "off", "dials": dict.fromkeys(talkback.DIALS, 50), "room": []}
    assert talkback.chat_lines(settings) == ""


def test_saved_settings_reach_the_writing_chat(tmp_path):
    talkback.save_settings(tmp_path, {"mode": "challenge", "dials": {"fast": 5, "grounded": 250}})
    settings = talkback.load_settings(tmp_path)
    assert settings["dials"]["grounded"] == 100 and settings["dials"]["affirm"] == 50
    lines = talkback.chat_lines(settings)
    assert lines.startswith("Talking back, mode: " + talkback.MODES["challenge"])
    assert "strongly answer in a sentence or two (5/100)" in lines
    assert "strongly go wild, with surprising leaps (100/100)" in lines
    talkback.save_settings(tmp_path, {"mode": "nonsense", "dials": {"fast": 80}})
    assert "mode:" not in talkback.chat_lines(talkback.load_settings(tmp_path))


def test_people_she_brings_in_are_named_to_the_writing_chat(tmp_path):
    talkback.save_settings(tmp_path, {"room": [" Iris Calder ", "Iris Calder", "Silas Reed", "", "A", "B", "C"]})
    settings = talkback.load_settings(tmp_path)
    assert settings["room"] == ["Iris Calder", "Silas Reed", "A", "B"]
    assert talkback.chat_lines(settings) == "In the conversation: Iris Calder, Silas Reed, A, B"
    talkback.save_settings(tmp_path, {**settings, "room": []})
    assert talkback.chat_lines(talkback.load_settings(tmp_path)) == ""


def test_the_chat_hears_who_joined_and_left():
    assert talkback.room_note(["A"], ["A"]) == ""
    note = talkback.room_note(["Iris Calder"], ["Mara Ellison"])
    assert note.startswith("[Talk to Write | who's in the conversation changed]")
    assert "Mara Ellison joined." in note and "Iris Calder left." in note
    assert "In the conversation: Mara Ellison" in note
