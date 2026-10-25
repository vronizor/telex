import json
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telex import app as app_module
from telex.radio import library

SCRIPT = """+++
date = {day}
+++

## intro

Good morning.

## bcn-rent-cap

The index is out. Landlords appeal.

## outro

That's Telex for today.
"""


def put_show(day, audio=True):
    settings = app_module.settings
    shows = library.shows_dir(settings.checkout)
    shows.mkdir(exist_ok=True)
    path = shows / f"{day}.md"
    path.write_text(SCRIPT.format(day=day))
    if audio:
        folder = library.audio_dir(settings.data_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day}.mp3").write_bytes(b"ID3" + bytes(5000))
        chapters = [
            {"start": 0, "kind": "intro", "topics": []},
            {
                "start": 3.2,
                "kind": "topic",
                "topics": [{"id": "bcn-rent-cap", "title": "Barcelona rent cap"}],
            },
            {"start": 75, "kind": "outro", "topics": []},
        ]
        meta = {"digest": library.digest(path), "duration": 80, "chapters": chapters}
        (folder / f"{day}.json").write_text(json.dumps(meta))


def test_no_show(client):
    assert "radio-strip" not in client.get("/").text
    assert "No show yet" in client.get("/radio").text


def test_recording_then_ready(client):
    day = app_module.store.now().date()
    put_show(day, audio=False)
    assert "recording…" in client.get("/").text
    assert "the Pi is recording it" in client.get("/radio").text
    put_show(day)
    home = client.get("/").text
    assert 'data-player data-day="' + str(day) in home and "2 segments" not in home
    page = client.get(f"/radio/{day}").text
    assert "Running order" in page and "Barcelona rent cap" in page
    assert f'href="/t/bcn-rent-cap/r/{day}"' in page
    assert "Landlords appeal." in page
    assert "&#34;start&#34;: 3.2" in page


def test_audio_supports_ranges(client):
    day = app_module.store.now().date()
    put_show(day)
    r = client.get(f"/radio/{day}.mp3")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    part = client.get(f"/radio/{day}.mp3", headers={"range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert client.get("/radio/2020-01-01.mp3").status_code == 404
    assert client.get("/radio/2020-01-01").status_code == 404


def test_old_shows_leave_the_home_page(client):
    day = app_module.store.now().date() - timedelta(days=3)
    put_show(day)
    assert "radio-strip" not in client.get("/").text
    assert day.strftime("%A %d %B") in client.get("/radio").text
    put_show(day + timedelta(days=1))
    assert f'href="/radio/{day}"' in client.get("/radio").text


def test_sync_delay_adds_pulls_after_dispatch():
    tz = ZoneInfo("Europe/Madrid")

    def minutes(h, m):
        now = datetime(2026, 10, 26, h, m, tzinfo=tz)
        return app_module.sync_delay(now, time(6, 45), 30) / 60

    assert minutes(6, 0) == 30
    assert minutes(6, 50) == 10
    assert minutes(7, 5) == 5
    assert minutes(7, 44) == 1
    assert minutes(8, 0) == 30
