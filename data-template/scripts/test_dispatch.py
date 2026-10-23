import tomllib
from datetime import date, timedelta

import dispatch as d
import pytest


def topic(id, cadence, slot=None, **extra):
    return {"id": id, "title": id, "cadence": cadence, "slot": slot, "brief": "b", **extra}


def add_report(root, id, day):
    folder = root / "reports" / id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.md").write_text('+++\nstatus = "quiet"\n+++\n')


@pytest.fixture
def root(tmp_path):
    for sub in ("topics", "reports", "queue"):
        (tmp_path / sub).mkdir()
    return tmp_path


THU = date(2026, 10, 22)


def test_last_and_next_scheduled():
    weekly_wed = topic("a", "weekly", 2)
    assert d.last_scheduled(weekly_wed, THU) == date(2026, 10, 21)
    assert d.next_scheduled_after(weekly_wed, THU) == date(2026, 10, 28)
    weekly_thu = topic("b", "weekly", 3)
    assert d.last_scheduled(weekly_thu, THU) == THU
    assert d.next_scheduled_after(weekly_thu, THU) == date(2026, 10, 29)
    monthly_5 = topic("c", "monthly", 5)
    assert d.last_scheduled(monthly_5, THU) == date(2026, 10, 5)
    assert d.next_scheduled_after(monthly_5, THU) == date(2026, 11, 5)
    monthly_28 = topic("e", "monthly", 28)
    assert d.last_scheduled(monthly_28, THU) == date(2026, 9, 28)
    assert d.last_scheduled(monthly_28, date(2026, 1, 3)) == date(2025, 12, 28)
    assert d.next_scheduled_after(topic("f", "daily"), THU) == date(2026, 10, 23)


def test_due_rules(root):
    t = topic("wed", "weekly", 2)
    d.write_topic(root, t)
    assert d.is_due(root, t, THU)
    add_report(root, "wed", date(2026, 10, 21))
    assert not d.is_due(root, t, THU)
    add_report(root, "wed", date(2026, 10, 14))
    assert not d.is_due(root, t, THU)
    (root / "queue" / "wed").touch()
    assert d.is_due(root, t, THU)
    d.done(root, "wed")
    assert not d.is_due(root, t, THU)
    assert d.is_due(root, t, date(2026, 10, 28))
    paused = topic("p", "daily", active=False)
    assert not d.is_due(root, paused, THU)


def test_missed_run_is_caught_up(root):
    t = topic("mon", "weekly", 0)
    add_report(root, "mon", date(2026, 10, 12))
    assert d.is_due(root, t, THU)


def test_pick_slot_balances():
    topics = [topic("a", "weekly", 0), topic("b", "weekly", 1), topic("c", "weekly", 0)]
    assert d.pick_slot(topics, "weekly") == 2
    topics += [topic(str(i), "weekly", i) for i in range(2, 7)]
    assert d.pick_slot(topics, "weekly") == 1
    assert d.pick_slot([topic("m", "monthly", 1)], "monthly") == 2
    assert d.pick_slot([], "daily") is None


def test_plan_assigns_orders_and_caps(root):
    d.write_topic(root, topic("old", "weekly", 0))
    add_report(root, "old", date(2026, 10, 12))
    d.write_topic(root, topic("fresh", "monthly"))
    d.write_topic(root, topic("asked", "weekly", 3))
    add_report(root, "asked", THU)
    (root / "queue" / "asked").touch()
    for i in range(3):
        d.write_topic(root, topic(f"daily{i}", "daily"))
    result = d.plan(root, THU, limit=4)
    assert result["slots_assigned"] == ["fresh"]
    assert [r["id"] for r in result["run"]] == ["asked", "daily0", "daily1", "daily2"]
    assert result["deferred"] == ["fresh", "old"]
    assert {t["id"]: t.get("slot") for t in d.load_topics(root)}["fresh"] == 1
    assert result["run"][0]["kind"] == "update"
    assert result["run"][0]["previous_report"] == f"reports/asked/{THU}.md"
    assert result["run"][1]["kind"] == "baseline"


def test_dump_round_trip(root):
    brief = "Line one with 'quotes' and \"doubles\".\nLine two: café, Barcelona — ok."
    t = topic(
        "rt", "weekly", 4, extra_languages=["ca"], local=False, brief=brief, title='A "title"'
    )
    path = d.write_topic(root, t)
    with path.open("rb") as f:
        data = tomllib.load(f)
    assert data["brief"] == brief
    assert data["title"] == 'A "title"'
    assert data["extra_languages"] == ["ca"]
    assert data["local"] is False
    assert "languages" not in data
    tricky = "has ''' inside\nand a newline"
    d.write_topic(root, topic("tr", "daily", brief=tricky))
    assert d.load_topics(root)[1]["brief"] == tricky


def test_rejects_bad_ids(root):
    with pytest.raises(ValueError):
        d.write_topic(root, topic("../evil", "daily"))
    with pytest.raises(ValueError):
        d.write_topic(root, topic("ok", "hourly"))


def test_describe_schedule():
    assert d.describe_schedule("weekly", 1) == "every Tue"
    assert d.describe_schedule("monthly", 22) == "monthly on the 22nd"
    assert d.describe_schedule("monthly", 11) == "monthly on the 11th"
    assert d.describe_schedule("daily", None) == "every day"
    assert d.describe_schedule("daily", None, 3) == "every 3 days"
    assert d.describe_schedule("weekly", 4, 2) == "every 2 weeks on Fri"
    assert d.describe_schedule("monthly", 5, 3) == "every 3 months on the 5th"


def test_until(root):
    t = topic("mid", "daily", until=date(2026, 11, 5))
    d.write_topic(root, t)
    loaded = d.load_topics(root)[0]
    assert loaded["until"] == date(2026, 11, 5)
    assert d.is_due(root, loaded, date(2026, 11, 5))
    assert not d.is_due(root, loaded, date(2026, 11, 6))


def test_next_due_with_every():
    wed2 = topic("w", "weekly", 2, every=2)
    assert d.next_due(wed2, date(2026, 10, 21)) == date(2026, 11, 4)
    assert d.next_due(wed2, date(2026, 10, 22)) == date(2026, 11, 4)
    assert d.next_due(topic("w1", "weekly", 0), date(2026, 10, 22)) == date(2026, 10, 26)
    assert d.next_due(topic("d3", "daily", every=3), date(2026, 10, 22)) == date(2026, 10, 25)
    assert d.next_due(topic("m2", "monthly", 5, every=2), date(2026, 10, 22)) == date(2026, 12, 5)
    assert d.next_due(topic("m1", "monthly", 1), date(2026, 10, 22)) == date(2026, 11, 1)
    assert d.next_due(topic("m1", "monthly", 1), date(2026, 12, 3)) == date(2027, 1, 1)


def test_every_due(root):
    t = topic("w", "weekly", 2, every=2)
    add_report(root, "w", date(2026, 10, 21))
    assert not d.is_due(root, t, date(2026, 10, 28))
    assert d.is_due(root, t, date(2026, 11, 4))


def test_write_keeps_until_and_every(root):
    d.write_topic(root, topic("k", "weekly", 1, every=3, until=date(2026, 12, 1)))
    d.write_topic(root, topic("k", "weekly", 1, brief="new"))
    loaded = d.load_topics(root)[0]
    assert (
        loaded["every"] == 3 and loaded["until"] == date(2026, 12, 1) and loaded["brief"] == "new"
    )
    d.write_topic(root, topic("k", "weekly", 1, every=1))
    assert "every" not in d.load_topics(root)[0]


def test_languages_migration_and_reader(root):
    (root / "topics" / "old.toml").write_text(
        'title = "Old"\ncadence = "daily"\nlanguages = ["fr", "en"]\nbrief = "b"\n'
    )
    t = d.load_topics(root)[0]
    assert "languages" not in t and t["local"] is True and t["extra_languages"] == []
    d.write_topic(root, t)
    assert "languages" not in (root / "topics" / "old.toml").read_text()
    assert d.plan(root, THU)["read"] == ["en"]
    (root / "reader.toml").write_text('read = ["en", "fr", "ca"]\n')
    result = d.plan(root, THU)
    assert result["read"] == ["en", "fr", "ca"]
    assert result["run"][0]["local"] is True


def test_load_sources(root):
    assert d.load_sources(root) == {}
    (root / "sources.toml").write_text(
        '[outlets."www.Blick.ch"]\nrole = "reporter"\ntier = "prefer"\nreach = "full"\n'
        '[outlets."rt.com"]\nrole = "voice"\ntier = "nope"\n'
    )
    s = d.load_sources(root)
    assert s["blick.ch"] == {"role": "reporter", "tier": "prefer", "reach": "full"}
    assert s["rt.com"] == {"role": "voice"}


def test_set_outlet_round_trip(root):
    (root / "sources.toml").write_text(
        '[outlets]\n[outlets."blick.ch"]\nrole = "reporter"\nlanguages = ["fr", "de"]\n'
        'reach = "full"\nchecked = 2026-10-23\nnote = "Swiss \\"tabloid\\""\n'
    )
    d.set_outlet(root, "www.Blick.ch", tier="prefer")
    d.set_outlet(root, "trtfrancais.com", role="voice", tier="avoid")
    s = d.load_sources(root)
    assert s["blick.ch"]["tier"] == "prefer" and s["blick.ch"]["note"] == 'Swiss "tabloid"'
    assert str(s["blick.ch"]["checked"]) == "2026-10-23"
    assert s["trtfrancais.com"] == {"role": "voice", "tier": "avoid"}
    d.set_outlet(root, "blick.ch", tier=None)
    assert "tier" not in d.load_sources(root)["blick.ch"]
    with pytest.raises(ValueError):
        d.set_outlet(root, "blick.ch", tier="love")
    with pytest.raises(ValueError):
        d.set_outlet(root, "../evil", tier="avoid")
    d.write_reader(root, ["fr", "xx", "en"])
    assert d.load_reader(root)["read"] == ["en", "fr"]


def test_reach_queue_record_history(root, tmp_path):
    (root / "sources.toml").write_text(
        '[outlets]\n[outlets."blick.ch"]\nrole = "reporter"\ntier = "prefer"\nreach = "full"\n'
    )
    d.queue_reach(root, "www.RTS.ch")
    assert d.reach_queue(root) == ["rts.ch"]
    assert d.plan(root, THU)["reach_queue"] == 1
    assert [c["domain"] for c in d.reach_plan(root)["check"]] == ["rts.ch"]
    assert [c["domain"] for c in d.reach_plan(root, everything=True)["check"]] == [
        "blick.ch",
        "rts.ch",
    ]
    results = tmp_path / "r.tsv"
    results.write_text(
        "domain\tsearch\treach\tnote\nrts.ch\ty\theadline\tfetch refused\nblick.ch\tyes\tfull\t\n"
    )
    changed = d.record_reach(root, THU, d.read_reach_rows(results))
    assert f"reach/history/{THU}.tsv" in changed and "reach/queue/rts.ch" in changed
    assert d.reach_queue(root) == []
    s = d.load_sources(root)
    assert s["rts.ch"]["reach"] == "headline" and str(s["rts.ch"]["checked"]) == str(THU)
    assert s["blick.ch"]["tier"] == "prefer"
    later = THU + timedelta(days=30)
    d.record_reach(root, later, [{"domain": "rts.ch", "search": "y", "reach": "full", "note": ""}])
    assert d.reach_history(root)["rts.ch"] == [(THU, "headline"), (later, "full")]
    bad = tmp_path / "bad.tsv"
    bad.write_text("domain\tsearch\treach\tnote\nrts.ch\ty\tmaybe\t\n")
    with pytest.raises(ValueError):
        d.read_reach_rows(bad)
