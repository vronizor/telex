import tomllib
from datetime import date

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
    t = topic("rt", "weekly", 4, languages=["ca", "es"], brief=brief, title='A "title"')
    path = d.write_topic(root, t)
    with path.open("rb") as f:
        data = tomllib.load(f)
    assert data["brief"] == brief
    assert data["title"] == 'A "title"'
    assert data["languages"] == ["ca", "es"]
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
