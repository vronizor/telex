import json
import tomllib

from telex import app as app_module
from telex.radio import library
from tests.conftest import remote_file

CATCHUP_REPORT = """+++
status = "catchup"
headline = "Labour governs on a shrinking base; Reform leads the polls"
window = "since the July 2024 election"
period_start = 2024-07-04
searched = ["en"]
+++

## Where it stands

Labour holds a large majority but trails in the polls. [BBC]

## Storylines

### The government's slide

Approval fell over the first year. [BBC]

### Reform's rise

Reform UK leads several polls. [GDN]

## Who's who

- **Keir Starmer** — prime minister. [BBC]

## Watch next

- Local elections in May.

## Left out

Scotland and Wales.

## Sources

- [BBC] [BBC: title](https://example.org/bbc) (en)
- [GDN] [Guardian: title](https://example.org/gdn) (en)
"""

SPECIAL = """+++
date = {day}
topic = "uk-politics"
title = "UK politics since the 2024 election"
+++

## intro

This is a Telex catch-up.

## story: The government's slide

Approval fell.

## story: Reform's rise

Reform leads.

## story: The leadership question

Questions remain.

## outro

Watch May.
"""


def add_catchup(client, title="UK politics", window="since the July 2024 election"):
    return client.post(
        "/catchup",
        data={"title": title, "window": window, "brief": "Mostly Westminster."},
        follow_redirects=False,
    )


def write_catchup_report(day):
    checkout = app_module.settings.checkout
    folder = checkout / "reports" / "uk-politics"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.md").write_text(CATCHUP_REPORT)


def write_special(day, audio=True):
    settings = app_module.settings
    folder = library.specials_dir(settings.checkout)
    folder.mkdir(exist_ok=True)
    path = folder / f"{day}-uk-politics.md"
    path.write_text(SPECIAL.format(day=day))
    if audio:
        key = f"specials/{day}-uk-politics"
        out = library.audio_dir(settings.data_dir) / "specials"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{day}-uk-politics.mp3").write_bytes(b"ID3" + bytes(5000))
        chapters = [
            {"start": 0, "kind": "intro", "topics": []},
            {"start": 20, "kind": "story", "topics": [], "label": "The government's slide"},
            {"start": 200, "kind": "story", "topics": [], "label": "Reform's rise"},
            {"start": 400, "kind": "outro", "topics": []},
        ]
        meta = {"digest": library.digest(path), "duration": 480, "chapters": chapters}
        library.write_meta(settings.data_dir, key, meta)
    return path


def test_create_catchup_commits_a_one_off_topic(client, remote):
    r = add_catchup(client)
    assert r.status_code == 303 and r.headers["location"] == "/t/uk-politics"
    data = tomllib.loads(remote_file(remote, "topics/uk-politics.toml"))
    assert data["catchup"] is True and data["window"] == "since the July 2024 election"
    assert data["brief"] == "Mostly Westminster." and "anchor" not in data
    page = client.get("/t/uk-politics").text
    assert "Catch-up over since the July 2024 election" in page
    assert "Keep following" not in page
    home = client.get("/").text
    assert 'action="/catchup"' in home and "CATCH-UP" in home


def test_catchup_validation_and_default_window(client, remote):
    r = client.post("/catchup", data={"title": "", "window": ""})
    assert r.status_code == 400 and "Say what you want" in r.text
    add_catchup(client, title="German coalition", window="")
    data = tomllib.loads(remote_file(remote, "topics/german-coalition.toml"))
    assert data["window"] == "the last six months"


def reset_reads(baseline="2026-01-01T00:00:00+01:00"):
    path = app_module.settings.data_dir / "read.json"
    path.write_text(json.dumps({"baseline": baseline}))
    app_module.reads._state = None


def test_catchup_report_special_and_follow(client, remote):
    reset_reads()
    add_catchup(client)
    day = app_module.store.now().date()
    write_catchup_report(day)
    write_special(day)
    page = client.get("/t/uk-politics").text
    assert "Listen to the special" in page and f"/radio/specials/{day}-uk-politics" in page
    assert "since 04.07.24" in page and "<h3>Reform's rise</h3>" in page
    assert 'href="/t/uk-politics/follow"' in page and "Redo catch-up" in page

    home = client.get("/").text
    assert "Catch-up · UK politics since the 2024 election" in home

    radio = client.get(f"/radio/specials/{day}-uk-politics")
    assert radio.status_code == 200
    assert "Read the catch-up" in radio.text and "Reform&#39;s rise" in radio.text
    assert f'src="/radio/specials/{day}-uk-politics.mp3' in radio.text
    assert client.get(f"/radio/specials/{day}-uk-politics.mp3").status_code == 200
    assert client.get("/radio/specials/nope").status_code == 404
    assert "── Catch-ups" in client.get("/radio").text

    played = client.post(f"/radio/specials/{day}-uk-politics/played", data={"state": "done"})
    assert played.status_code == 204
    assert "Catch-up · UK politics" not in client.get("/").text

    follow = client.get("/t/uk-politics/follow")
    assert follow.status_code == 200 and 'name="follow" value="1"' in follow.text
    r = client.post(
        "/t/uk-politics/edit",
        data={
            "follow": "1",
            "title": "UK politics",
            "brief": "Westminster votes and polls.",
            "cadence": "weekly",
            "every": "1",
            "local": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    data = tomllib.loads(remote_file(remote, "topics/uk-politics.toml"))
    assert "catchup" not in data and "window" not in data and "anchor" in data
    topic = app_module.store.get("uk-politics")
    assert not topic.catchup and topic.next_run is not None
    plan = app_module.store.d.plan(app_module.settings.checkout, topic.next_run)
    entry = next(e for e in plan["run"] if e["id"] == "uk-politics")
    assert entry["kind"] == "update"
    assert entry["previous_report"] == f"reports/uk-politics/{day}.md"


def test_editing_a_catchup_keeps_it_a_catchup(client, remote):
    add_catchup(client)
    form = client.get("/t/uk-politics/edit")
    assert "Edit catch-up" in form.text
    client.post(
        "/t/uk-politics/edit",
        data={"title": "UK politics", "window": "the last year", "brief": ""},
        follow_redirects=False,
    )
    data = tomllib.loads(remote_file(remote, "topics/uk-politics.toml"))
    assert data["catchup"] is True and data["window"] == "the last year"


def test_special_state_and_listing(client):
    day = app_module.store.now().date()
    write_special(day, audio=False)
    settings = app_module.settings
    [show] = library.specials(settings.checkout, settings.data_dir)
    assert show.special and show.state == "recording" and show.topic == "uk-politics"
    assert show.title == "UK politics since the 2024 election"
    write_special(day)
    show = library.show_state(settings.checkout, settings.data_dir, show.key)
    assert show.state == "ready"
    assert [c.title for c in show.topic_chapters] == ["The government's slide", "Reform's rise"]
    meta = json.loads((library.audio_dir(settings.data_dir) / f"{show.key}.json").read_text())
    assert meta["duration"] == 480
