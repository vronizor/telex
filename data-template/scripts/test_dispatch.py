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
    assert sorted(result["slots_assigned"]) == [
        "asked",
        "daily0",
        "daily1",
        "daily2",
        "fresh",
        "old",
    ]
    anchors = {t["id"]: t.get("anchor") for t in d.load_topics(root)}
    assert anchors["old"] == date(2026, 10, 19)
    assert anchors["asked"] == date(2026, 10, 29)
    assert all(anchors.values())
    assert [r["id"] for r in result["run"]] == ["asked", "daily0", "daily1", "daily2"]
    assert result["deferred"] == ["fresh", "old"]
    fresh = {t["id"]: t for t in d.load_topics(root)}["fresh"]
    assert fresh["slot"] == fresh["anchor"].day and fresh["anchor"] >= THU + timedelta(days=15)
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


def add_status(root, id, day, status):
    folder = root / "reports" / id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.md").write_text(f'+++\nstatus = "{status}"\n+++\n')


GOOD_SHOW = """+++
date = 2026-10-25
+++

## intro

Good morning. This is Telex for Sunday, the twenty-fifth of October.

## alpha

The council voted on Friday to raise rents by four percent. That is decided, not proposed.

Only Catalan-language outlets report the second vote; nobody has confirmed it yet.

## quiet: beta, gamma

Nothing material today on beta or gamma.

## outro

That's Telex for today.
"""


def write_show(root, text, day="2026-10-25"):
    (root / "shows").mkdir(exist_ok=True)
    path = root / "shows" / f"{day}.md"
    path.write_text(text)
    return path


@pytest.fixture
def show_root(root):
    day = date(2026, 10, 25)
    add_status(root, "alpha", day, "update")
    add_status(root, "beta", day, "quiet")
    add_status(root, "gamma", day, "quiet")
    add_status(root, "delta", day - timedelta(days=1), "update")
    return root


def test_parse_show_segments():
    show = d.parse_show(GOOD_SHOW)
    assert show["date"] == date(2026, 10, 25)
    assert [(s["kind"], s["topics"]) for s in show["segments"]] == [
        ("intro", []),
        ("topic", ["alpha"]),
        ("quiet", ["beta", "gamma"]),
        ("outro", []),
    ]
    assert len(show["segments"][1]["paragraphs"]) == 2


def test_check_show_accepts_a_good_show(show_root):
    result = d.check_show(show_root, write_show(show_root, GOOD_SHOW))
    assert result["ok"], result["problems"]
    assert result["words"] > 30


def test_check_show_rejects_written_forms(show_root):
    text = GOOD_SHOW.replace("four percent", "4% [LVG]").replace(
        "That is decided", "(only in ca sources) — that costs $5/month. That is decided"
    )
    problems = d.check_show(show_root, write_show(show_root, text))["problems"]
    assert len(problems) == 1
    for token in ("4%", "[LVG]", "(only", "$5/month."):
        assert token in problems[0]


def test_check_show_rejects_other_scripts_and_markdown(show_root):
    text = GOOD_SHOW.replace("Nothing material", "**Nothing** material in ליכוד").replace(
        "That's Telex for today.", "- That's Telex for today."
    )
    problems = d.check_show(show_root, write_show(show_root, text))["problems"]
    assert any("**Nothing**" in p and "ליכוד" in p for p in problems)
    assert any("list or quote" in p for p in problems)


def test_check_show_coverage(show_root):
    text = GOOD_SHOW.replace("## quiet: beta, gamma", "## quiet: beta, alpha\n\nX.\n\n## delta")
    problems = d.check_show(show_root, write_show(show_root, text))["problems"]
    assert "'alpha' is not quiet (update): give it its own segment" in problems
    assert "'alpha' appears more than once" in problems
    assert "'delta' has no report dated 2026-10-25" in problems
    assert "today's report 'gamma' is missing from the show" in problems


def test_check_show_structure_and_lengths(show_root):
    long_sentence = " ".join(["word"] * 45) + "."
    text = GOOD_SHOW.replace("## intro", "## alpha2").replace(
        "That is decided, not proposed.", long_sentence + " " + " ".join(["more."] * 240)
    )
    problems = d.check_show(show_root, write_show(show_root, text))["problems"]
    assert "the show must open with exactly one '## intro'" in problems
    assert any("45-word sentence" in p for p in problems)
    assert any(p.startswith("[alpha] has") and "limit is 280" in p for p in problems)


def test_check_show_date_and_preamble(show_root):
    wrong_day = d.check_show(show_root, write_show(show_root, GOOD_SHOW, "2026-10-24"))
    assert "front matter needs date = 2026-10-24" in wrong_day["problems"]
    preamble = GOOD_SHOW.replace("## intro", "Hello.\n\n## intro")
    result = d.check_show(show_root, write_show(show_root, preamble))
    assert result["problems"] == ["text before the first '## ' segment heading"]
    assert not d.check_show(show_root, show_root / "shows" / "2026-10-30.md")["ok"]


def test_all_quiet_show(root):
    day = date(2026, 10, 25)
    add_status(root, "beta", day, "quiet")
    text = """+++
date = 2026-10-25
+++

## intro

Good morning. A short one today.

## quiet: beta

Nothing material on beta.

## outro

Back tomorrow.
"""
    assert d.check_show(root, write_show(root, text))["ok"]


TUE = date(2026, 10, 27)


def test_grid_is_forward_only_and_steps_by_period():
    t = topic("a", "daily", every=2, anchor=TUE)
    assert d.grid(t, date(2026, 10, 20), date(2026, 11, 2)) == [
        TUE,
        date(2026, 10, 29),
        date(2026, 10, 31),
        date(2026, 11, 2),
    ]
    w = topic("w", "weekly", 0, every=3, anchor=date(2026, 11, 9))
    assert d.grid(w, THU, date(2026, 12, 31)) == [
        date(2026, 11, 9),
        date(2026, 11, 30),
        date(2026, 12, 21),
    ]
    m = topic("m", "monthly", 26, every=2, anchor=date(2026, 11, 26))
    assert d.grid(m, THU, date(2027, 3, 31)) == [
        date(2026, 11, 26),
        date(2027, 1, 26),
        date(2027, 3, 26),
    ]


def test_anchored_due_dates(root):
    t = topic("a", "daily", every=2, anchor=date(2026, 10, 28))
    add_report(root, "a", date(2026, 10, 25))
    assert not d.is_due(root, t, TUE)
    assert d.is_due(root, t, date(2026, 10, 28))
    assert d.next_due(t, date(2026, 10, 25)) == date(2026, 10, 28)
    add_report(root, "a", date(2026, 10, 28))
    assert not d.is_due(root, t, date(2026, 10, 29))
    assert d.is_due(root, t, date(2026, 10, 31)), "a missed run is caught up the next day"
    assert not d.is_due(root, {**t, "until": date(2026, 10, 29)}, date(2026, 10, 30))


def test_refresh_does_not_cause_back_to_back_runs():
    w = topic("w", "weekly", 1, anchor=TUE)
    assert d.next_due(w, date(2026, 11, 2)) == date(2026, 11, 10)
    assert d.next_due(w, date(2026, 10, 30)) == date(2026, 11, 3)


def test_pick_anchor_spreads_topics(root):
    a = topic("a", "daily", every=2, anchor=TUE)
    d.write_topic(root, a)
    b = topic("b", "daily", every=2)
    assert d.pick_anchor(root, [a], b, TUE) == date(2026, 10, 28)
    weekly = [topic(f"w{i}", "weekly", i, anchor=TUE + timedelta(days=i)) for i in range(6)]
    for w in weekly:
        d.write_topic(root, w)
    new = topic("n", "weekly")
    assert (
        d.pick_anchor(root, weekly, new, TUE, TUE).weekday() == (TUE + timedelta(days=6)).weekday()
    )


def test_write_topic_keeps_anchor_only_while_the_rhythm_is_unchanged(root):
    d.write_topic(root, topic("a", "daily", every=2, anchor=TUE))
    d.write_topic(root, topic("a", "daily", every=2, title="renamed"))
    assert d.load_topics(root)[0]["anchor"] == TUE
    d.write_topic(root, topic("a", "daily", every=3))
    assert "anchor" not in d.load_topics(root)[0]
    d.write_topic(root, d.with_anchor(topic("b", "weekly"), TUE))
    b = [t for t in d.load_topics(root) if t["id"] == "b"][0]
    assert (b["slot"], b["anchor"]) == (1, TUE)


def test_rebalance_and_forecast(root):
    for id in ("x", "y"):
        d.write_topic(root, topic(id, "daily", every=2, anchor=TUE))
        add_report(root, id, date(2026, 10, 25))
    assert d.forecast(root, TUE, 2) == [["x", "y"], []]
    moves = d.rebalance(root, TUE, apply=True)
    assert len(moves) == 1 and moves[0]["to"] == "2026-10-28"
    assert d.forecast(root, TUE, 2) in ([["x"], ["y"]], [["y"], ["x"]])
    assert d.rebalance(root, TUE) == []


def test_anchor_errors():
    w = topic("w", "weekly")
    assert d.anchor_error(w, date(2026, 10, 26), TUE)
    assert d.anchor_error(topic("m", "monthly"), date(2026, 10, 30), TUE)
    assert d.anchor_error(w, date(2026, 10, 28), TUE) is None


def test_the_anchor_itself_is_always_honoured():
    m = topic("m", "monthly", 1, anchor=date(2026, 11, 1))
    assert d.next_due(m, date(2026, 10, 22)) == date(2026, 11, 1)
    assert d.next_due(m, date(2026, 11, 1)) == date(2026, 12, 1)
    assert d.next_due(m, date(2026, 11, 20)) == date(2027, 1, 1)


def test_end_date_bounds_the_next_report(root):
    t = topic("e", "weekly", every=3, until=date(2026, 11, 5))
    assert d.pick_anchor(root, [], t, TUE, date(2026, 10, 30)) <= date(2026, 11, 5)
    assert "end date" in d.anchor_error(t, date(2026, 11, 12), TUE)
