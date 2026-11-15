import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telex import app as app_module
from telex.reads import Reads, is_new
from tests.test_radio_app import put_show
from tests.test_wire import FakeFetch, rss, setup_feeds, w

TZ = ZoneInfo("Europe/Madrid")


def since(iso: str) -> None:
    path = app_module.settings.data_dir / "read.json"
    path.write_text(json.dumps({"baseline": iso}))
    app_module.reads._state = None


def test_first_load_marks_everything_read(tmp_path):
    now = datetime(2026, 11, 15, 10, 0, tzinfo=TZ)
    reads = Reads(tmp_path, lambda: now)
    assert reads.report_read("x") == date(2026, 11, 15)
    assert reads.show(date(2026, 11, 15)) == "done"
    assert reads.show(date(2026, 11, 16)) is None
    assert json.loads((tmp_path / "read.json").read_text())["baseline"].startswith("2026-11-15")


def test_marks_only_move_forward(tmp_path):
    (tmp_path / "read.json").write_text(json.dumps({"baseline": "2026-10-01T00:00:00+02:00"}))
    reads = Reads(tmp_path, lambda: None)
    reads.mark_report("x", date(2026, 10, 20))
    reads.mark_report("x", date(2026, 10, 10))
    assert reads.report_read("x") == date(2026, 10, 20)
    day = date(2026, 10, 21)
    reads.mark_show(day, "done")
    reads.mark_show(day, "started")
    assert reads.show(day) == "done"
    assert Reads(tmp_path, lambda: None).report_read("x") == date(2026, 10, 20)


def test_is_new_tolerates_bad_stamps():
    cut = datetime(2026, 10, 1, tzinfo=TZ)
    assert is_new({"seen": "2026-10-02T08:00:00+02:00"}, cut)
    assert not is_new({"seen": "2026-09-30T08:00:00+02:00"}, cut)
    assert not is_new({"seen": "2026-10-02T08:00:00"}, cut)
    assert not is_new({"seen": "junk"}, cut)


def test_unread_reports_until_opened(client):
    since("2026-10-01T00:00:00+02:00")
    home = client.get("/").text
    assert "Unread · 1" in home and 'class="item is-unread" href="/t/bcn-rent-cap"' in home
    assert 'aria-label="Unread"' in client.get("/sources").text
    older = client.get("/t/bcn-rent-cap/r/2026-10-14").text
    assert older.count('class="dot"') >= 2
    assert "Unread · 1" in client.get("/").text
    client.get("/t/bcn-rent-cap")
    home = client.get("/").text
    assert "Unread" not in home and 'aria-label="Unread"' not in home


def test_quiet_reports_stay_read(client):
    since("2026-10-01T00:00:00+02:00")
    home = client.get("/").text
    assert 'href="/t/eu-chat-control"' in home
    assert 'class="item is-unread" href="/t/eu-chat-control"' not in home


def test_mark_all_read(client):
    since("2026-10-01T00:00:00+02:00")
    r = client.post("/read/all", follow_redirects=False)
    assert r.status_code == 303
    assert "Unread" not in client.get("/").text


def test_radio_dot_until_finished(client):
    day = app_module.store.now().date()
    since((datetime.combine(day, datetime.min.time(), TZ) - timedelta(days=1)).isoformat())
    put_show(day)
    home = client.get("/").text
    assert f'data-show-dot="{day}"' in home and "data-tab-dot" in home
    assert client.post(f"/radio/{day}/played", data={"state": "started"}).status_code == 204
    home = client.get("/").text
    assert "dot dot-started" in home and "data-tab-dot" not in home
    client.post(f"/radio/{day}/played", data={"state": "done"})
    assert f'data-show-dot="{day}"' not in client.get(f"/radio/{day}").text
    assert client.post(f"/radio/{day}/played", data={"state": "x"}).status_code == 400
    assert client.post("/radio/2020-01-01/played", data={"state": "done"}).status_code == 404


def test_wire_counts_new_headlines(client):
    since("2026-10-01T00:00:00+02:00")
    setup_feeds(client, {"rts.ch": ["https://www.rts.ch/a"]})
    app_module.wire.fetcher = FakeFetch(
        {"https://www.rts.ch/a": w.Fetched(200, rss(("1", "Generalitat", "")))}
    )
    app_module.wire.pull(app_module.store.now())
    assert "1 new on wire" in client.get("/").text
    page = client.get("/t/bcn-rent-cap").text
    assert 'class="is-new"' in page and "1 new</span>" in page
    assert "new on wire" not in client.get("/").text
    assert 'class="is-new"' not in client.get("/t/bcn-rent-cap").text
