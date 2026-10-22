import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

from briefings import app as app_module
from briefings import edition

TZ = ZoneInfo("Europe/Madrid")


def test_edition_day_for():
    s = app_module.settings
    assert edition.edition_day_for(datetime(2026, 10, 22, 12, tzinfo=TZ), s) == date(2026, 10, 17)
    assert edition.edition_day_for(datetime(2026, 10, 24, 7, tzinfo=TZ), s) == date(2026, 10, 17)
    assert edition.edition_day_for(datetime(2026, 10, 24, 9, tzinfo=TZ), s) == date(2026, 10, 24)
    assert edition.edition_day_for(datetime(2026, 10, 26, 9, tzinfo=TZ), s) == date(2026, 10, 24)


def add_report(store, topic, day, text):
    folder = store.root / "reports" / topic
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.md").write_text(text)


def test_collect_and_build(client):
    store, settings = app_module.store, app_module.settings
    ed = edition.collect(store, date(2026, 10, 17))
    assert [(t.id, t.state) for t in ed.topics] == [
        ("bcn-rent-cap", "update"),
        ("eu-chat-control", "quiet"),
    ]
    assert ed.summary == "1 update · 1 quiet"
    assert ed.range_label == "11–17 Oct 2026"
    meta = edition.ensure(store, settings, date(2026, 10, 17))
    path = edition.editions_dir(settings) / "2026-10-17.epub"
    with zipfile.ZipFile(path) as z:
        assert z.read("mimetype") == b"application/epub+zip"
        names = z.namelist()
        chapter = next(z.read(n).decode() for n in names if n.endswith("topic_0.xhtml"))
        toc = next(z.read(n).decode() for n in names if n.endswith("contents.xhtml"))
    assert "Story so far" in chapter
    assert 'href="style/style.css"' in chapter and 'href="style/style.css"' in toc
    assert 'href="#r1014-src-GENCAT"' in chapter
    assert 'id="r1014-src-GENCAT"' in chapter
    assert "CONTESTED" in chapter
    assert "Quiet this week" in toc and "EU chat control" in toc
    assert edition.ensure(store, settings, date(2026, 10, 17))["built_at"] == meta["built_at"]


def test_multiple_reports_in_a_week(client):
    store = app_module.store
    from tests.conftest import REPORT_UPDATE

    add_report(
        store,
        "bcn-rent-cap",
        "2026-10-23",
        REPORT_UPDATE.replace("New reference index published", "Court rejects suspension"),
    )
    ed = edition.collect(store, date(2026, 10, 24))
    bcn = ed.topics[0]
    assert [r.day.isoformat() for r in bcn.reports] == ["2026-10-21", "2026-10-23"]
    html = edition.chapter(bcn)
    assert html.count('<h2 class="day">') == 2
    assert html.count("── Where it stands") == 1
    assert html.count("── Story so far") == 1
    assert "Court rejects suspension" in html


def test_opds_feed_and_files(client):
    edition.ensure(app_module.store, app_module.settings, date(2026, 10, 17))
    r = client.get("/opds/", headers={"x-forwarded-host": "briefings.my-tailnet.ts.net"})
    assert r.status_code == 200
    assert "opds-catalog" in r.headers["content-type"]
    assert "https://briefings.my-tailnet.ts.net/opds/edition/2026-10-17.epub" in r.text
    assert "Briefings · 11–17 Oct 2026" in r.text
    epub_r = client.get("/opds/edition/2026-10-17.epub")
    assert epub_r.status_code == 200 and epub_r.content[:2] == b"PK"
    assert client.get("/opds/cover/2026-10-17.png").content[:4] == b"\x89PNG"
    assert client.get("/opds/edition/2020-01-01.epub").status_code == 404
    assert client.get("/opds/edition/..%2Fsecrets.epub").status_code in (404, 422)
    assert "Edition 11–17 Oct 2026" in client.get("/").text
