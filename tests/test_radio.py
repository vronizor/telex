import json
import os
import shutil
import time
from datetime import date

import pytest

from telex.radio import library, watch

DAY = date(2026, 10, 25)
SHOW = """+++
date = 2026-10-25
+++

## intro

Good morning.

## alpha

First paragraph.

Second paragraph.

## quiet: beta

Nothing on beta.

## outro

Bye.
"""


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "data" / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(
        os.path.join(os.path.dirname(__file__), "..", "data-template", "scripts", "dispatch.py"),
        root / "scripts" / "dispatch.py",
    )
    (root / "topics").mkdir()
    (root / "topics" / "alpha.toml").write_text('title = "Alpha topic"\ncadence = "daily"\n')
    for topic, status in (("alpha", "update"), ("beta", "quiet")):
        (root / "reports" / topic).mkdir(parents=True)
        (root / "reports" / topic / f"{DAY}.md").write_text(f'+++\nstatus = "{status}"\n+++\n')
    (root / "shows").mkdir()
    return root


def write_show(checkout, text=SHOW, day=DAY, age_s=60):
    path = checkout / "shows" / f"{day}.md"
    path.write_text(text)
    old = time.time() - age_s
    os.utime(path, (old, old))
    return path


def test_pending_waits_for_settled_new_or_changed_scripts(checkout):
    data_dir = checkout.parent
    path = write_show(checkout, age_s=1)
    assert watch.pending(checkout, data_dir, DAY, 14) == []
    write_show(checkout)
    [(day, digest)] = watch.pending(checkout, data_dir, DAY, 14)
    assert day == DAY and digest == library.digest(path)
    library.write_meta(data_dir, DAY, {"digest": digest, "duration": 1})
    assert watch.pending(checkout, data_dir, DAY, 14) == []
    write_show(checkout, SHOW.replace("Bye.", "Goodbye."))
    assert len(watch.pending(checkout, data_dir, DAY, 14)) == 1
    write_show(checkout, day=date(2026, 10, 1))
    assert [d for d, _ in watch.pending(checkout, data_dir, DAY, 14)] == [DAY]


def test_failure_is_not_retried_until_the_script_changes(checkout):
    data_dir = checkout.parent
    path = write_show(checkout)
    library.record_failure(data_dir, DAY, library.digest(path), "boom\nOOM")
    assert watch.pending(checkout, data_dir, DAY, 14) == []
    show = library.show_state(checkout, data_dir, DAY)
    assert show.state == "failed" and show.error.endswith("OOM")
    write_show(checkout, SHOW.replace("Bye.", "Goodbye."))
    assert len(watch.pending(checkout, data_dir, DAY, 14)) == 1
    assert library.show_state(checkout, data_dir, DAY).state == "recording"


def test_show_states_and_prune(checkout):
    data_dir = checkout.parent
    assert library.show_state(checkout, data_dir, DAY) is None
    path = write_show(checkout)
    assert library.show_state(checkout, data_dir, DAY).state == "recording"
    audio = library.audio_dir(data_dir)
    audio.mkdir()
    (audio / f"{DAY}.mp3").write_bytes(b"ID3")
    chapters = [
        {"start": 0, "kind": "intro", "topics": []},
        {"start": 4.5, "kind": "topic", "topics": [{"id": "alpha", "title": "Alpha topic"}]},
        {"start": 60, "kind": "quiet", "topics": [{"id": "beta", "title": "beta"}]},
    ]
    meta = {"digest": library.digest(path), "duration": 90, "chapters": chapters}
    library.write_meta(data_dir, DAY, meta)
    show = library.show_state(checkout, data_dir, DAY)
    assert (show.state, show.minutes, show.stale) == ("ready", 2, False)
    assert [c.title for c in show.topic_chapters] == ["Alpha topic", "Quiet: beta"]
    write_show(checkout, SHOW.replace("Bye.", "Goodbye."))
    assert library.show_state(checkout, data_dir, DAY).stale
    (audio / "2026-10-01.mp3").write_bytes(b"x")
    (audio / "2026-10-01.json").write_text("{}")
    removed = watch.prune(data_dir, DAY, 14)
    assert sorted(p.name for p in removed) == ["2026-10-01.json", "2026-10-01.mp3"]
    assert [s.day for s in library.recent_shows(checkout, data_dir, DAY, 14)] == [DAY]


def test_render_with_a_fake_engine(checkout):
    np = pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    from telex.radio import render

    class Fake:
        sample_rate = 8000
        info = {"engine": "fake"}

        def synth(self, text):
            return np.full(len(text) * 10, 0.5, dtype=np.float32)

    data_dir = checkout.parent
    write_show(checkout)
    show = render.dispatch(checkout).load_show(checkout / "shows" / f"{DAY}.md")
    audio, chapters = render.render(show, Fake(), {"alpha": "Alpha topic"})
    assert [c["kind"] for c in chapters] == ["intro", "topic", "quiet", "outro"]
    assert chapters[1]["topics"] == [{"id": "alpha", "title": "Alpha topic"}]
    assert chapters[1]["start"] == pytest.approx(
        (len("Good morning.") * 10 + 9600) / 8000, abs=0.01
    )
    assert float(abs(audio).max()) == pytest.approx(render.PEAK)
    meta = render.render_day(checkout, data_dir, DAY, Fake())
    assert meta["problems"] == [] and meta["digest"] == library.digest(
        checkout / "shows" / f"{DAY}.md"
    )
    assert (library.audio_dir(data_dir) / f"{DAY}.mp3").stat().st_size > 0
    stored = json.loads((library.audio_dir(data_dir) / f"{DAY}.json").read_text())
    assert stored["duration"] == meta["duration"]
    assert library.show_state(checkout, data_dir, DAY).state == "ready"
