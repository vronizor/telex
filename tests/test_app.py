import subprocess
import tomllib

from telex.store import render_markdown
from tests.conftest import git, remote_file


def test_index_lists_topics(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="new"' in r.text and 'action="/new"' in r.text
    assert "New reference index published" in r.text
    assert "Paris cycling network" in r.text
    assert "1 quiet" in r.text
    assert 'class="c-add">+2' in r.text
    assert "telex_seen" in r.headers["set-cookie"]


def test_topic_page_renders_diff(client):
    r = client.get("/t/bcn-rent-cap")
    assert r.status_code == 200
    assert 'class="d d-add"' in r.text
    assert 'class="d d-contested"' in r.text
    assert 'class="d d-removed"' in r.text
    assert '<a class="src" href="https://example.org/a">[GENCAT]</a>' in r.text
    assert 'id="src-LVG"' in r.text
    assert 'href="javascript:' not in r.text
    assert "src 4" in r.text
    assert "/t/bcn-rent-cap/r/2026-10-14" in r.text
    older = client.get("/t/bcn-rent-cap/r/2026-10-14")
    assert "Reading the" in older.text
    assert client.get("/t/bcn-rent-cap/r/2020-01-01").status_code == 404
    assert client.get("/t/nope").status_code == 404


def test_pending_topic_page(client):
    r = client.get("/t/paris-velo")
    assert "No report yet" in r.text


def test_create_topic_commits_and_pushes(client, remote):
    r = client.post(
        "/new",
        data={
            "title": "Réforme des retraites (CH)",
            "brief": "Primary sources.\nSeparate decided from proposed.",
            "cadence": "weekly",
            "languages": ["fr", "de"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/t/reforme-des-retraites-ch"
    text = remote_file(remote, "topics/reforme-des-retraites-ch.toml")
    data = tomllib.loads(text)
    assert data["brief"] == "Primary sources.\nSeparate decided from proposed."
    assert data["languages"] == ["fr", "de"]
    assert data["slot"] not in (2, 4)
    assert data["active"] is True
    again = client.post(
        "/new",
        data={
            "title": "Réforme des retraites (CH)",
            "brief": "x",
            "cadence": "daily",
            "languages": ["fr"],
        },
        follow_redirects=False,
    )
    assert again.headers["location"] == "/t/reforme-des-retraites-ch-2"


def test_validation(client, remote):
    r = client.post(
        "/new", data={"title": "X", "brief": "", "cadence": "weekly", "languages": ["en"]}
    )
    assert r.status_code == 400
    assert "Say how this topic should be reported" in r.text
    r = client.post("/new", data={"title": "X", "brief": "y", "cadence": "weekly"})
    assert "at least one language" in r.text
    assert remote_file(remote, "topics/x.toml") is None


def test_edit_keeps_or_reassigns_slot(client, remote):
    client.post(
        "/t/bcn-rent-cap/edit",
        data={
            "title": "Barcelona rent cap",
            "brief": "New brief",
            "cadence": "weekly",
            "languages": ["ca"],
            "active": "on",
        },
    )
    data = tomllib.loads(remote_file(remote, "topics/bcn-rent-cap.toml"))
    assert data["slot"] == 2 and data["brief"] == "New brief"
    assert str(data["created"]) == "2026-09-30"
    client.post(
        "/t/bcn-rent-cap/edit",
        data={
            "title": "Barcelona rent cap",
            "brief": "New brief",
            "cadence": "monthly",
            "languages": ["ca"],
        },
    )
    data = tomllib.loads(remote_file(remote, "topics/bcn-rent-cap.toml"))
    assert data["cadence"] == "monthly" and data["slot"] == 1
    assert data["active"] is False


def test_refresh_queues_once(client, remote):
    r = client.post("/t/eu-chat-control/refresh", follow_redirects=False)
    assert r.status_code == 303
    assert remote_file(remote, "queue/eu-chat-control") == ""
    count = lambda: subprocess.run(  # noqa: E731
        ["git", "--git-dir", str(remote), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    before = count()
    client.post("/t/eu-chat-control/refresh")
    assert count() == before
    assert "Refresh requested" in client.get("/t/eu-chat-control").text


def test_cross_site_post_refused(client):
    r = client.post("/sync", data={"back": "/"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    ok = client.post(
        "/sync",
        data={"back": "/"},
        headers={"origin": "http://telex.test"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_sync_pulls_new_reports(client, remote, tmp_path):
    other = tmp_path / "other"
    git("clone", "-q", str(remote), str(other))
    folder = other / "reports" / "eu-chat-control"
    (folder / "2026-10-23.md").write_text(
        '+++\nstatus = "update"\nheadline = "Vote scheduled"\n+++\n'
    )
    git("add", "-A", cwd=other)
    git("commit", "-q", "-m", "report", cwd=other)
    git("push", "-q", cwd=other)
    assert "Vote scheduled" not in client.get("/").text
    client.post("/sync", data={"back": "//evil.example"}, follow_redirects=False)
    assert "Vote scheduled" in client.get("/").text


def test_render_markdown_markers():
    html = render_markdown("- [+] new [link](https://x.org)\n- [~] revised\n- plain\n")
    assert '<li class="d d-add"><span class="d-mark" title="add">+</span>' in html
    assert '<a href="https://x.org">link</a>' in html
    assert 'class="d d-revised"' in html
    assert "<li>plain</li>" in html
    assert "<script>" not in render_markdown("<script>alert(1)</script>")


def test_post_through_tailscale_serve(client):
    r = client.post(
        "/sync",
        data={"back": "/"},
        headers={
            "host": "127.0.0.1:8020",
            "x-forwarded-host": "telex.my-tailnet.ts.net",
            "origin": "https://telex.my-tailnet.ts.net",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_edit_keeps_until(client, remote):
    import tomllib as _t

    from telex import app as app_module
    from tests.conftest import git

    path = app_module.store.root / "topics" / "eu-chat-control.toml"
    path.write_text(path.read_text() + "until = 2026-11-05\n")
    git("commit", "-qam", "until", cwd=app_module.store.root)
    git("push", "-q", cwd=app_module.store.root)
    page = client.get("/t/eu-chat-control").text
    assert "until 5 Nov" in page
    client.post(
        "/t/eu-chat-control/edit",
        data={
            "title": "EU chat control",
            "brief": "x",
            "cadence": "weekly",
            "languages": ["en"],
            "active": "on",
        },
    )
    data = _t.loads(remote_file(remote, "topics/eu-chat-control.toml"))
    assert str(data["until"]) == "2026-11-05"


def test_chat_link_and_copy(client):
    import re as _re
    from urllib.parse import unquote

    page = client.get("/t/bcn-rent-cap").text
    m = _re.search(r'href="https://claude\.ai/new\?q=([^"]+)"', page)
    assert m
    prompt = unquote(m.group(1).replace("&amp;", "&"))
    assert len(prompt) <= 1800
    assert prompt.startswith('I\'m reading my Telex briefing on "Barcelona rent cap"')
    assert "Story so far:" in prompt and "What changed:" in prompt
    assert "[GENCAT] https://example.org/a" in prompt
    assert "suggest three questions" in prompt
    assert 'data-copy="# Barcelona rent cap' in page
    assert "Chat about this" not in client.get("/t/paris-velo").text


def test_chat_link_points_to_report(client, monkeypatch):
    import dataclasses
    import re as _re
    import subprocess
    from urllib.parse import unquote

    from telex import app as app_module

    gh = dataclasses.replace(app_module.settings, repo_url="git@github.com:you/telex-data.git")
    monkeypatch.setattr(app_module, "settings", gh)
    page = client.get("/t/bcn-rent-cap").text
    m = _re.search(r'href="https://claude\.ai/new\?q=([^"]+)"', page)
    prompt = unquote(m.group(1).replace("&amp;", "&"))
    day = app_module.store.get("bcn-rent-cap").latest.day.isoformat()
    sha = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", f"reports/bcn-rent-cap/{day}.md"],
        cwd=app_module.store.root,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert sha
    assert f"GitHub repo you/telex-data at commit {sha}" in prompt
    assert f"file reports/bcn-rent-cap/{day}.md" in prompt
    assert "[GENCAT]" not in prompt


def test_github_repo_parsing():
    from telex.config import Settings

    def slug(url):
        return Settings(url, None, None, None, 0, "", "").github_repo

    assert slug("git@github.com:you/telex-data.git") == "you/telex-data"
    assert slug("https://github.com/you/telex-data") == "you/telex-data"
    assert slug("ssh://git@github.com/you/telex-data.git") == "you/telex-data"
    assert slug("/tmp/remote.git") is None


def test_every_n(client, remote):
    import tomllib as _t

    from tests.conftest import remote_file as _rf

    r = client.post(
        "/new",
        data={
            "title": "Swiss pensions",
            "brief": "x",
            "cadence": "weekly",
            "every": "2",
            "languages": ["fr"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    data = _t.loads(_rf(remote, "topics/swiss-pensions.toml"))
    assert data["every"] == 2
    page = client.get("/t/swiss-pensions").text
    assert "every 2 weeks on" in page
    bad = client.post(
        "/new",
        data={"title": "Y", "brief": "x", "cadence": "daily", "every": "0", "languages": ["fr"]},
    )
    assert bad.status_code == 400 and "Repeat every 1 to 12 days" in bad.text


def test_tags_starting_with_digit():
    from telex.store import render_markdown

    html = render_markdown(
        "Text [3CAT] and [2026] stays.\n\n## Sources\n\n"
        "- [3CAT] [3Cat news](https://example.org/3) (ca)\n"
    )
    assert '<a class="src" href="https://example.org/3">[3CAT]</a>' in html
    assert 'id="src-3CAT"' in html
    assert "[2026] stays" in html


def test_until_from_form(client, remote):
    import tomllib as _t

    from tests.conftest import remote_file as _rf

    base = {
        "title": "EU chat control",
        "brief": "x",
        "cadence": "weekly",
        "every": "1",
        "languages": ["en"],
        "active": "on",
    }
    client.post("/t/eu-chat-control/edit", data={**base, "until": "2026-12-31"})
    data = _t.loads(_rf(remote, "topics/eu-chat-control.toml"))
    assert str(data["until"]) == "2026-12-31"
    assert 'value="2026-12-31"' in client.get("/t/eu-chat-control/edit").text
    client.post("/t/eu-chat-control/edit", data={**base, "until": ""})
    assert "until" not in _t.loads(_rf(remote, "topics/eu-chat-control.toml"))
    bad = client.post("/t/eu-chat-control/edit", data={**base, "until": "soon"})
    assert bad.status_code == 400
