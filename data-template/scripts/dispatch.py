"""Scheduling and file-format helpers shared by the dispatcher run and the web app. Stdlib only."""

import argparse
import json
import re
import sys
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")
CADENCES = ("daily", "weekly", "monthly")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LANGUAGES = ("en", "fr", "es", "ca", "de", "it")
MAX_PER_RUN = 4
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


def today_local() -> date:
    return datetime.now(TZ).date()


def load_topics(root: Path = ROOT) -> list[dict]:
    topics = []
    for path in sorted((root / "topics").glob("*.toml")):
        with path.open("rb") as f:
            data = tomllib.load(f)
        data["id"] = path.stem
        data.setdefault("active", True)
        data.setdefault("languages", ["en"])
        topics.append(data)
    return topics


def report_dates(root: Path, topic_id: str) -> list[date]:
    folder = root / "reports" / topic_id
    dates = []
    for path in folder.glob("*.md"):
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(dates)


def is_queued(root: Path, topic_id: str) -> bool:
    return (root / "queue" / topic_id).exists()


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def last_scheduled(topic: dict, today: date) -> date:
    cadence, slot = topic["cadence"], topic.get("slot")
    if cadence == "daily" or slot is None:
        return today
    if cadence == "weekly":
        return today - timedelta(days=(today.weekday() - slot) % 7)
    this_month = today.replace(day=slot)
    return this_month if today.day >= slot else _shift_month(today, -1).replace(day=slot)


def next_scheduled_after(topic: dict, day: date) -> date:
    cadence, slot = topic["cadence"], topic.get("slot")
    if cadence == "daily" or slot is None:
        return day + timedelta(days=1)
    if cadence == "weekly":
        return day + timedelta(days=(slot - day.weekday() - 1) % 7 + 1)
    this_month = day.replace(day=slot)
    return this_month if day.day < slot else _shift_month(day, 1).replace(day=slot)


def has_ended(topic: dict, today: date) -> bool:
    until = topic.get("until")
    return isinstance(until, date) and today > until


def is_due(root: Path, topic: dict, today: date) -> bool:
    if not topic.get("active", True) or has_ended(topic, today):
        return False
    if is_queued(root, topic["id"]):
        return True
    dates = report_dates(root, topic["id"])
    return not dates or dates[-1] < last_scheduled(topic, today)


def slot_loads(topics: list[dict], cadence: str) -> list[int]:
    size = 7 if cadence == "weekly" else 28
    loads = [0] * size
    for t in topics:
        live = t.get("active", True) and not has_ended(t, today_local())
        if live and t["cadence"] == cadence and t.get("slot") is not None:
            index = t["slot"] if cadence == "weekly" else t["slot"] - 1
            if 0 <= index < size:
                loads[index] += 1
    return loads


def pick_slot(topics: list[dict], cadence: str) -> int | None:
    if cadence == "daily":
        return None
    loads = slot_loads(topics, cadence)
    best = min(range(len(loads)), key=lambda i: (loads[i], i))
    return best if cadence == "weekly" else best + 1


def describe_schedule(cadence: str, slot: int | None) -> str:
    if cadence == "daily":
        return "every day"
    if slot is None:
        return f"{cadence}, day not set yet"
    if cadence == "weekly":
        return f"every {WEEKDAYS[slot]}"
    return f"monthly on the {slot}{_suffix(slot)}"


def _suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _toml_string(value: str) -> str:
    if (
        "\n" in value
        and "'''" not in value
        and not any(ord(c) < 32 and c not in "\n\t" for c in value)
    ):
        return "'''\n" + value.rstrip("\n") + "'''"
    return json.dumps(value, ensure_ascii=False)


def dump_topic(topic: dict) -> str:
    lines = [
        f"title = {_toml_string(topic['title'])}",
        f"cadence = {json.dumps(topic['cadence'])}",
    ]
    if topic.get("slot") is not None:
        lines.append(f"slot = {int(topic['slot'])}")
    lines += [
        f"languages = {json.dumps(list(topic.get('languages') or ['en']))}",
        f"active = {'true' if topic.get('active', True) else 'false'}",
        f"created = {topic.get('created') or today_local()}",
    ]
    if isinstance(topic.get("until"), date):
        lines.append(f"until = {topic['until']}")
    lines += [
        f"brief = {_toml_string(topic['brief'].strip())}",
    ]
    return "\n".join(lines) + "\n"


def write_topic(root: Path, topic: dict) -> Path:
    if not SLUG_RE.match(topic["id"]):
        raise ValueError(f"bad topic id: {topic['id']!r}")
    if topic["cadence"] not in CADENCES:
        raise ValueError(f"bad cadence: {topic['cadence']!r}")
    path = root / "topics" / f"{topic['id']}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_topic(topic), encoding="utf-8")
    return path


def assign_missing_slots(root: Path) -> list[str]:
    topics = load_topics(root)
    changed = []
    for t in topics:
        if t["cadence"] != "daily" and t.get("slot") is None:
            t["slot"] = pick_slot(topics, t["cadence"])
            write_topic(root, t)
            changed.append(t["id"])
    return changed


def plan(root: Path, today: date, limit: int = MAX_PER_RUN) -> dict:
    assigned = assign_missing_slots(root)
    due = []
    for t in load_topics(root):
        if not is_due(root, t, today):
            continue
        dates = report_dates(root, t["id"])
        due.append(
            {
                "id": t["id"],
                "title": t["title"],
                "languages": t["languages"],
                "queued": is_queued(root, t["id"]),
                "previous_report": f"reports/{t['id']}/{dates[-1]}.md" if dates else None,
                "kind": "update" if dates else "baseline",
                "_order": (not is_queued(root, t["id"]), bool(dates), (dates or [date.min])[-1]),
            }
        )
    due.sort(key=lambda d: d.pop("_order"))
    return {
        "today": today.isoformat(),
        "slots_assigned": assigned,
        "run": due[:limit],
        "deferred": [d["id"] for d in due[limit:]],
    }


def done(root: Path, topic_id: str) -> None:
    (root / "queue" / topic_id).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dispatch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--today", type=date.fromisoformat, default=None)
    p_plan.add_argument("--max", type=int, default=MAX_PER_RUN)
    p_done = sub.add_parser("done")
    p_done.add_argument("topic_id")
    args = parser.parse_args(argv)
    if args.cmd == "plan":
        result = plan(ROOT, args.today or today_local(), args.max)
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        done(ROOT, args.topic_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
