import json
from datetime import datetime, time
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import pytest

from telex import wire as w
from tests.conftest import remote_file

TZ = ZoneInfo("Europe/Madrid")

RSS = b"""
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Le Monde</title>
<item>
  <title>Loyers : la Generalitat publie l&#8217;indice</title>
  <link>https://www.lemonde.fr/a.html</link>
  <guid isPermaLink="false">lm-1</guid>
  <description>&lt;p&gt;Premier paragraphe.&lt;/p&gt;&lt;p&gt;Second &amp;amp; dernier.&lt;/p&gt;
  </description>
  <content:encoded>&lt;p&gt;FULL ARTICLE TEXT&lt;/p&gt;</content:encoded>
  <pubDate>Mon, 26 Oct 2026 06:30:00 +0100</pubDate>
</item>
<item>
  <title>Via atom link</title>
  <atom:link href="https://www.lemonde.fr/b.html"/>
  <pubDate>not a date</pubDate>
</item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
<item><title></title><link>https://www.lemonde.fr/untitled.html</link></item>
</channel></rss>
"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>NZZ</title>
  <entry>
    <title type="html">Bundesrat &lt;b&gt;entscheidet&lt;/b&gt;</title>
    <link rel="enclosure" href="https://www.nzz.ch/img.jpg"/>
    <link rel="alternate" href="https://www.nzz.ch/schweiz/x"/>
    <id>tag:nzz.ch,2026:x</id>
    <updated>2026-10-26T05:00:00Z</updated>
    <summary>Kurz.</summary>
    <content type="html">VOLLTEXT</content>
  </entry>
</feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <item rdf:about="https://www.dw.com/en/a">
    <title>DW story</title>
    <link>https://www.dw.com/en/a</link>
    <description>Text</description>
    <dc:date>2026-10-26T04:00:00Z</dc:date>
  </item>
</rdf:RDF>"""


def test_parse_rss_keeps_summary_not_body():
    items = w.parse_feed(RSS)
    assert [i["url"] for i in items] == [
        "https://www.lemonde.fr/a.html",
        "https://www.lemonde.fr/b.html",
    ]
    first = items[0]
    assert first["id"] == "lm-1"
    assert first["title"] == "Loyers : la Generalitat publie l’indice"
    assert first["summary"] == "Premier paragraphe. Second & dernier."
    assert first["published"] == "2026-10-26T06:30:00+01:00"
    assert "FULL ARTICLE" not in json.dumps(items)
    assert items[1]["published"] is None
    assert items[1]["id"] == "https://www.lemonde.fr/b.html"


def test_parse_atom_and_rdf():
    (entry,) = w.parse_feed(ATOM)
    assert entry["url"] == "https://www.nzz.ch/schweiz/x"
    assert entry["title"] == "Bundesrat entscheidet"
    assert entry["summary"] == "Kurz."
    assert entry["published"] == "2026-10-26T05:00:00+00:00"
    assert "VOLLTEXT" not in json.dumps(entry)
    (item,) = w.parse_feed(RDF)
    assert item["id"] == "https://www.dw.com/en/a"
    assert item["published"] == "2026-10-26T04:00:00+00:00"


def test_parse_rejects_non_feeds():
    with pytest.raises(ET.ParseError):
        w.parse_feed(b"<html><body>Not a feed")
    assert w.parse_feed(b"<html><body><p>hi</p></body></html>") == []


def test_clip_cuts_at_a_word():
    text = "word " * 200
    out = w.clip(text.strip(), 50)
    assert len(out) <= 50 and out.endswith("word…")
    assert w.clip("short", 50) == "short"


def at(hh, mm, day=26):
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


def test_next_run_hourly_with_publish_before_dispatch():
    dispatch = time(6, 45)
    assert w.next_run(at(0, 2), dispatch, 60) == (at(0, 7), False)
    assert w.next_run(at(5, 7), dispatch, 60) == (at(6, 7), False)
    assert w.next_run(at(6, 7), dispatch, 60) == (at(6, 30), True)
    assert w.next_run(at(6, 30), dispatch, 60) == (at(7, 7), False)
    assert w.next_run(at(23, 30), dispatch, 60) == (at(0, 7, 27), False)
    assert w.next_run(at(6, 10), dispatch, 15) == (at(6, 22), False)


class FakeFetch:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url, etag=None, modified=None):
        self.calls.append((url, etag, modified))
        response = self.responses[url]
        return response(etag) if callable(response) else response


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>https://www.rts.ch/{n}</link><guid>{n}</guid>"
        f"<description>{s}</description></item>"
        for n, t, s in items
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


def setup_feeds(client, feeds: dict[str, list[str]]):
    from telex import app as app_module

    root = app_module.store.root
    lines = []
    for domain, urls in feeds.items():
        lines += [f'[outlets."{domain}"]', f"feeds = {json.dumps(urls)}", 'languages = ["fr"]', ""]
    (root / "sources.toml").write_text("[outlets]\n\n" + "\n".join(lines))
    d = app_module.store.d
    d.set_watch(root, "bcn-rent-cap", ["Generalitat", "loyer*"])
    return app_module


def test_pull_dedupes_matches_and_publishes(client, remote):
    app_module = setup_feeds(client, {"rts.ch": ["https://www.rts.ch/info/?format=rss/news"]})
    url = "https://www.rts.ch/info/?format=rss/news"
    first = w.Fetched(
        200,
        rss(("1", "La Generalitat publie l'indice", "s"), ("2", "Football", "Servette gagne")),
        etag='"v1"',
    )
    fake = FakeFetch({url: first})
    hub = w.Wire(app_module.settings.data_dir, app_module.store, fetcher=fake, sleep=lambda s: None)
    monday = at(9, 7)
    out = hub.pull(monday)
    assert out["new"] == 2 and out["matched"] == {"bcn-rent-cap": 1}
    fake.responses[url] = w.Fetched(
        200, rss(("1", "La Generalitat publie l'indice", "s"), ("3", "Les loyers montent", "x"))
    )
    out = hub.pull(at(10, 7))
    assert fake.calls[-1][1] == '"v1"'
    assert out["new"] == 1 and out["matched"] == {"bcn-rent-cap": 2}
    fake.responses[url] = w.Fetched(304)
    assert hub.pull(at(11, 7))["new"] == 0
    hub2 = w.Wire(
        app_module.settings.data_dir, app_module.store, fetcher=fake, sleep=lambda s: None
    )
    fake.responses[url] = w.Fetched(200, rss(("3", "Les loyers montent", "x")))
    assert hub2.pull(at(12, 7))["new"] == 0
    state = hub.state()[url]
    assert state["ok"].startswith("2026-10-26T12:07") and state["new"] == {"2026-10-26": 3}
    assert "gap" not in state
    items, total = hub.for_topic("bcn-rent-cap", None)
    assert total == 2 and items[0]["title"] == "Les loyers montent"
    assert items[0]["matched"] == ["loyer*"] and items[0]["lang"] == "fr"
    hub.publish(app_module.repo)
    text = remote_file(remote, "wire/bcn-rent-cap.jsonl")
    assert text.count("\n") == 2 and "Servette" not in text


def test_pull_records_errors_and_gaps(client):
    a, b = "https://www.rts.ch/a", "https://www.rts.ch/b"
    app_module = setup_feeds(client, {"rts.ch": [a, b]})
    fake = FakeFetch({a: w.Fetched(0, error="timed out"), b: w.Fetched(200, rss(("1", "x", "")))})
    sleeps = []
    hub = w.Wire(app_module.settings.data_dir, app_module.store, fetcher=fake, sleep=sleeps.append)
    out = hub.pull(at(9, 7))
    assert out["failed"] == 1 and sleeps == [w.SAME_HOST_PAUSE]
    fake.responses[a] = w.Fetched(200, b"<html>")
    fake.responses[b] = w.Fetched(200, rss(("9", "y", "")))
    hub.pull(at(10, 7))
    state = hub.state()
    assert state[a]["errors"] == 2 and state[a]["error"].startswith("not a feed")
    assert state[b]["gap"].startswith("2026-10-26T10:07")


def test_rematch_drops_topics_without_terms(client):
    app_module = setup_feeds(client, {"rts.ch": ["https://www.rts.ch/a"]})
    fake = FakeFetch({"https://www.rts.ch/a": w.Fetched(200, rss(("1", "Generalitat", "")))})
    hub = w.Wire(app_module.settings.data_dir, app_module.store, fetcher=fake, sleep=lambda s: None)
    hub.pull(at(9, 7))
    assert (app_module.settings.data_dir / "wire" / "bcn-rent-cap.jsonl").exists()
    app_module.store.d.set_watch(app_module.store.root, "bcn-rent-cap", [])
    assert hub.rematch(at(9, 8).date()) == {}
    assert not (app_module.settings.data_dir / "wire" / "bcn-rent-cap.jsonl").exists()


def test_discover_reads_alternate_links():
    page = b"""<html><head>
      <link rel="alternate" type="application/rss+xml" title="Toute l'info"
            href="/info/?format=rss">
      <link rel="Alternate" type="application/atom+xml" href="https://cdn.example.org/atom">
      <link rel="stylesheet" href="/x.css">
      <link rel="alternate" type="application/rss+xml" href="javascript:alert(1)">
    </head></html>"""
    fake = FakeFetch({"https://www.rts.ch/": w.Fetched(200, page)})
    found = w.discover("www.rts.ch", fetcher=fake)
    assert found == [
        {"title": "Toute l'info", "url": "https://www.rts.ch/info/?format=rss"},
        {"title": "", "url": "https://cdn.example.org/atom"},
    ]
    with pytest.raises(ValueError):
        w.discover("x.ch", fetcher=FakeFetch({"https://x.ch/": w.Fetched(0, error="refused")}))


def test_check_summarises_a_feed():
    fake = FakeFetch({"u": w.Fetched(200, RSS), "v": w.Fetched(404, error="HTTP 404")})
    row = w.check("u", fetcher=fake)
    assert row["items"] == 2 and row["with_summary"] == 1
    assert row["newest"] == "2026-10-26T06:30:00+01:00"
    assert w.check("v", fetcher=fake)["error"] == "HTTP 404"


def test_topic_page_lists_wire_items(client):
    app_module = setup_feeds(client, {"rts.ch": ["https://www.rts.ch/a"]})
    fake = FakeFetch(
        {"https://www.rts.ch/a": w.Fetched(200, rss(("1", "Generalitat &amp;amp; co", "")))}
    )
    app_module.wire.fetcher = fake
    app_module.wire.pull(app_module.store.now())
    page = client.get("/t/bcn-rent-cap").text
    assert 'class="wire"' in page and "Generalitat &amp; co" in page
    assert "On the wire since" in page
    assert 'class="wire"' not in client.get("/t/eu-chat-control").text


def test_form_saves_watch_terms(client, remote):
    import tomllib

    r = client.post(
        "/t/eu-chat-control/edit",
        data={
            "title": "EU chat control",
            "brief": "Votes only.",
            "cadence": "weekly",
            "every": "1",
            "watch": "chat control\r\nCSAM & regulation\r\n\r\nchat control",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    data = tomllib.loads(remote_file(remote, "topics/eu-chat-control.toml"))
    assert data["watch"] == ["chat control", "CSAM & regulation"]
    assert "chat control\nCSAM &amp; regulation" in client.get("/t/eu-chat-control/edit").text


def test_sources_feed_add_remove_and_health(client, remote):
    from telex import app as app_module

    r = client.post(
        "/sources/feeds",
        data={"domain": "rts.ch", "url": "https://www.rts.ch/info/?format=rss/news"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/sources?show=feeds")
    assert "https://www.rts.ch/info/?format=rss/news" in remote_file(remote, "sources.toml")
    page = client.get("/sources?show=feeds").text
    assert "www.rts.ch/info/?format=rss/news" in page and "not fetched yet" in page
    bad = client.post("/sources/feeds", data={"domain": "rts.ch", "url": "ftp://x"})
    assert bad.status_code == 400
    app_module.wire.fetcher = FakeFetch(
        {"https://www.rts.ch/info/?format=rss/news": w.Fetched(0, error="timed out")}
    )
    app_module.wire.pull(app_module.store.now())
    assert "failing: timed out" in client.get("/sources?show=feeds").text
    client.post(
        "/sources/feeds",
        data={
            "domain": "rts.ch",
            "url": "https://www.rts.ch/info/?format=rss/news",
            "action": "remove",
        },
    )
    assert "format=rss/news" not in remote_file(remote, "sources.toml")


def test_sources_find_feeds(client, monkeypatch):
    from telex import app as app_module

    page = b'<link rel="alternate" type="application/rss+xml" title="Info" href="/rss">'
    monkeypatch.setattr(
        app_module,
        "discover",
        lambda domain: w.discover(domain, FakeFetch({f"https://{domain}/": w.Fetched(200, page)})),
    )
    client.post("/sources/outlet", data={"domain": "rts.ch", "tier": "prefer"})
    html = client.get("/sources?show=all&find=rts.ch").text
    assert "Advertised on the homepage" in html and 'value="https://rts.ch/rss"' in html
