"""Scheduling and file-format helpers shared by the dispatcher run and the web app. Stdlib only."""

import argparse
import csv
import json
import re
import sys
import tomllib
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Madrid")
CADENCES = ("daily", "weekly", "monthly")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LANGUAGES = ("en", "fr", "es", "ca", "de", "it")
MAX_PER_RUN = 4
MAX_LOCAL = 3
MAX_REACH_PER_RUN = 10
REACH_COLUMNS = ("domain", "search", "reach", "note")
MAX_EVERY = 12
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
        data.setdefault("local", True)
        data["extra_languages"] = list(data.get("extra_languages", []))
        data.pop("languages", None)
        topics.append(data)
    return topics


def load_reader(root: Path = ROOT) -> dict:
    path = root / "reader.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["read"] = list(data.get("read") or ["en"])
    return data


ROLES = ("reporter", "voice")
TIERS = ("prefer", "avoid")
REACH = ("full", "partial", "headline", "none")


def load_sources(root: Path = ROOT) -> dict[str, dict]:
    path = root / "sources.toml"
    if not path.exists():
        return {}
    outlets = tomllib.loads(path.read_text(encoding="utf-8")).get("outlets", {})
    clean = {}
    for domain, info in outlets.items():
        info = dict(info)
        for key, allowed in (("role", ROLES), ("tier", TIERS), ("reach", REACH)):
            if info.get(key) not in allowed:
                info.pop(key, None)
        clean[domain.lower().removeprefix("www.")] = info
    return clean


OUTLET_KEYS = ("role", "tier", "languages", "country", "reach", "checked", "note")
DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _toml_value(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return json.dumps([str(v) for v in value], ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def dump_sources(outlets: dict[str, dict]) -> str:
    order = {"prefer": 0, None: 1, "avoid": 2}
    lines = [
        '# Outlets the dispatcher treats specially, keyed by domain (no "www.").',
        '# role: "reporter" | "voice"        tier: "prefer" | "avoid" (omit for neutral)',
        '# reach: "full" | "partial" | "headline" | "none", as of `checked`',
        "",
        "[outlets]",
    ]
    for domain in sorted(outlets, key=lambda d: (order.get(outlets[d].get("tier"), 1), d)):
        info = outlets[domain]
        lines += ["", f'[outlets."{domain}"]']
        keys = [k for k in OUTLET_KEYS if info.get(k) not in (None, "", [])]
        lines += [f"{k} = {_toml_value(info[k])}" for k in keys]
    return "\n".join(lines) + "\n"


def set_outlet(root: Path, domain: str, **changes) -> Path:
    domain = clean_domain(domain)
    outlets = load_sources(root)
    info = outlets.setdefault(domain, {})
    for key, value in changes.items():
        allowed = {"role": ROLES, "tier": TIERS}.get(key)
        if allowed is None or (value is not None and value not in allowed):
            raise ValueError(f"bad {key}: {value!r}")
        if value is None:
            info.pop(key, None)
        else:
            info[key] = value
    path = root / "sources.toml"
    path.write_text(dump_sources(outlets), encoding="utf-8")
    return path


def write_reader(root: Path, read: list[str]) -> Path:
    langs = [lang for lang in LANGUAGES if lang in read] or ["en"]
    path = root / "reader.toml"
    path.write_text(f"read = {json.dumps(langs)}\n", encoding="utf-8")
    return path


def clean_domain(domain: str) -> str:
    domain = domain.strip().lower().removeprefix("www.")
    if not DOMAIN_RE.match(domain):
        raise ValueError(f"bad domain: {domain!r}")
    return domain


def reach_queue(root: Path = ROOT) -> list[str]:
    folder = root / "reach" / "queue"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file() and DOMAIN_RE.match(p.name))


def queue_reach(root: Path, domain: str) -> Path:
    path = root / "reach" / "queue" / clean_domain(domain)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def reach_history(root: Path = ROOT) -> dict[str, list[tuple[date, str]]]:
    history: dict[str, dict[date, str]] = {}
    for path in sorted((root / "reach" / "history").glob("*.tsv")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row.get("reach") in REACH and row.get("domain"):
                    history.setdefault(row["domain"], {})[day] = row["reach"]
    return {d: sorted(days.items()) for d, days in history.items()}


def read_reach_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            reach = (row.get("reach") or "").strip()
            if reach not in REACH:
                raise ValueError(f"bad reach for {row.get('domain')!r}: {reach!r}")
            search = (row.get("search") or "").strip().lower()
            rows.append(
                {
                    "domain": clean_domain(row.get("domain") or ""),
                    "search": "y" if search.startswith("y") else "n",
                    "reach": reach,
                    "note": " ".join((row.get("note") or "").split())[:120],
                }
            )
    return rows


def record_reach(root: Path, day: date, rows: list[dict]) -> list[str]:
    if not rows:
        return []
    history = root / "reach" / "history" / f"{day.isoformat()}.tsv"
    history.parent.mkdir(parents=True, exist_ok=True)
    new = not history.exists()
    with history.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REACH_COLUMNS, delimiter="\t", lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerows(rows)
    outlets = load_sources(root)
    changed = [history]
    for row in rows:
        info = outlets.setdefault(row["domain"], {})
        info["reach"] = row["reach"]
        info["checked"] = day
        queued = root / "reach" / "queue" / row["domain"]
        if queued.exists():
            queued.unlink()
            changed.append(queued)
    sources = root / "sources.toml"
    sources.write_text(dump_sources(outlets), encoding="utf-8")
    changed.append(sources)
    return [str(p.relative_to(root)) for p in changed]


def reach_plan(root: Path, everything: bool = False, limit: int = MAX_REACH_PER_RUN) -> dict:
    outlets = load_sources(root)
    queued = reach_queue(root)
    domains = sorted(set(outlets) | set(queued)) if everything else queued[:limit]
    return {
        "check": [
            {"domain": d, "languages": list(outlets.get(d, {}).get("languages", []))}
            for d in domains
        ],
        "deferred": [] if everything else queued[limit:],
    }


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


def every(topic: dict) -> int:
    try:
        n = int(topic.get("every", 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, MAX_EVERY))


def legacy_next_due(topic: dict, last: date) -> date:
    """Schedule before anchors: N days, weeks or months after the last report, on the slot."""
    n, cadence, slot = every(topic), topic["cadence"], topic.get("slot")
    if cadence == "daily":
        return last + timedelta(days=n)
    if cadence == "weekly":
        target = last + timedelta(days=7 * n)
        if slot is None:
            return target
        return target - timedelta(days=(target.weekday() - slot) % 7)
    month = _shift_month(last, n)
    return month.replace(day=slot) if slot is not None else month


def period_days(cadence: str, n: int) -> int:
    return {"daily": 1, "weekly": 7, "monthly": 30}[cadence] * n


def min_gap(topic: dict) -> int:
    return max(1, period_days(topic["cadence"], every(topic)) // 2)


def anchor_of(topic: dict) -> date | None:
    anchor = topic.get("anchor")
    return anchor if isinstance(anchor, date) and not isinstance(anchor, datetime) else None


def grid(topic: dict, start: date, end: date, anchor: date | None = None) -> list[date]:
    """Scheduled days in [start, end]: the anchor, then every N days, weeks or months after it."""
    anchor = anchor or anchor_of(topic)
    if anchor is None or end < anchor:
        return []
    start = max(start, anchor)
    n, cadence = every(topic), topic["cadence"]
    if cadence in ("daily", "weekly"):
        step = period_days(cadence, n)
        first = anchor + timedelta(days=-(-(start - anchor).days // step) * step)
        return [first + timedelta(days=i * step) for i in range((end - first).days // step + 1)]
    days, k = [], 0
    while True:
        day = _shift_month(anchor, k * n).replace(day=anchor.day)
        if day > end:
            return days
        if day >= start:
            days.append(day)
        k += 1


def next_due(topic: dict, last: date) -> date:
    """First day a topic is due again after a report on `last`."""
    if anchor_of(topic) is None:
        return legacy_next_due(topic, last)
    anchor = anchor_of(topic)
    if anchor > last:
        return anchor
    earliest = last + timedelta(days=min_gap(topic))
    for horizon in (400, 4000):
        days = grid(topic, earliest, earliest + timedelta(days=horizon))
        if days:
            return days[0]
    return earliest


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


def due_on(topic: dict, day: date, last: date | None, queued: bool = False) -> bool:
    if not topic.get("active", True) or has_ended(topic, day):
        return False
    if queued or last is None:
        return True
    if anchor_of(topic) is None:
        return day >= legacy_next_due(topic, last)
    return day >= next_due(topic, last)


def is_due(root: Path, topic: dict, today: date) -> bool:
    dates = report_dates(root, topic["id"])
    return due_on(topic, today, dates[-1] if dates else None, is_queued(root, topic["id"]))


def forecast(
    root: Path, start: date, days: int, topics: list[dict] | None = None
) -> list[list[str]]:
    """Topic ids due on each of `days` days from `start`, ignoring the per-run cap."""
    topics = load_topics(root) if topics is None else topics
    out: list[list[str]] = [[] for _ in range(days)]
    for t in topics:
        if not t.get("active", True):
            continue
        dates = report_dates(root, t["id"])
        last = dates[-1] if dates else None
        queued = is_queued(root, t["id"])
        due = start if queued or last is None else next_due(t, last)
        while (due - start).days < days and not has_ended(t, max(due, start)):
            day = max(due, start)
            out[(day - start).days].append(t["id"])
            due = next_due(t, day)
    return out


def candidate_anchors(cadence: str, n: int, earliest: date) -> list[date]:
    if cadence in ("daily", "weekly"):
        return [earliest + timedelta(days=i) for i in range(period_days(cadence, n))]
    out = []
    for k in range(n + 1):
        month = _shift_month(earliest, k)
        out += [month.replace(day=dd) for dd in range(1, 29) if month.replace(day=dd) >= earliest]
    return out[: 28 * n]


def pick_anchor(
    root: Path,
    topics: list[dict],
    topic: dict,
    start: date,
    earliest: date | None = None,
    base: list[int] | None = None,
) -> date:
    """The anchor from `earliest` that keeps the busiest coming day as light as possible."""
    n, cadence = every(topic), topic["cadence"]
    horizon = min(400, max(84, 2 * period_days(cadence, n) + 31))
    if base is None:
        others = [t for t in topics if t["id"] != topic.get("id")]
        base = [len(ids) for ids in forecast(root, start, horizon, others)]
    earliest = max(earliest or start, start)
    until = topic.get("until") if isinstance(topic.get("until"), date) else None
    end = start + timedelta(days=len(base) - 1)
    if until:
        end = min(end, until)

    def score(anchor: date) -> tuple:
        loads = list(base)
        for day in grid(topic, start, end, anchor):
            loads[(day - start).days] += 1
        return (max(loads), sum(x * x for x in loads), anchor)

    candidates = candidate_anchors(cadence, n, earliest)
    if until:
        candidates = [c for c in candidates if c <= until] or candidates
    return min(candidates, key=score)


def with_anchor(topic: dict, anchor: date) -> dict:
    topic = {**topic, "anchor": anchor}
    if topic["cadence"] == "weekly":
        topic["slot"] = anchor.weekday()
    elif topic["cadence"] == "monthly":
        topic["slot"] = anchor.day
    else:
        topic.pop("slot", None)
    return topic


def anchor_error(topic: dict, anchor: date, start: date) -> str | None:
    if anchor < start:
        return f"The next report can't be before {start:%a %d %b}."
    if topic["cadence"] == "monthly" and anchor.day > 28:
        return "Monthly topics run on day 1 to 28."
    until = topic.get("until")
    if isinstance(until, date) and anchor > until:
        return f"The next report is after the end date ({until:%a %d %b})."
    return None


def rebalance(root: Path, start: date, apply: bool = False) -> list[dict]:
    """Move topics whose schedule can make the busiest coming days lighter, most frequent first."""
    topics = [t for t in load_topics(root) if t.get("active", True) and not has_ended(t, start)]
    topics.sort(key=lambda t: (period_days(t["cadence"], every(t)), t["id"]))
    moves = []
    for t in topics:
        if anchor_of(t) is None:
            continue
        dates = report_dates(root, t["id"])
        last = dates[-1] if dates else None
        current = next_due(t, last) if last else anchor_of(t)
        horizon = min(400, max(84, 2 * period_days(t["cadence"], every(t)) + 31))
        others = [o for o in load_topics(root) if o["id"] != t["id"]]
        base = [len(ids) for ids in forecast(root, start, horizon, others)]
        earliest = max(start, last + timedelta(days=min_gap(t))) if last else start
        best = pick_anchor(root, others, t, start, earliest, base)

        def peak(anchor: date, topic=t, base=base) -> tuple:
            loads = list(base)
            for day in grid(topic, start, start + timedelta(days=len(base) - 1), anchor):
                loads[(day - start).days] += 1
            return (max(loads), sum(x * x for x in loads))

        if current is not None and current >= start and peak(best) < peak(current):
            moves.append({"id": t["id"], "from": current.isoformat(), "to": best.isoformat()})
            if apply:
                write_topic(root, with_anchor(t, best))
    return moves


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


def slot_label(cadence: str, slot: int | None) -> str | None:
    if slot is None or cadence == "daily":
        return None
    return WEEKDAYS[slot] if cadence == "weekly" else f"{slot}{_suffix(slot)}"


def describe_schedule(cadence: str, slot: int | None, n: int = 1) -> str:
    unit = {"daily": "day", "weekly": "week", "monthly": "month"}[cadence]
    base = f"every {unit}" if n == 1 else f"every {n} {unit}s"
    if cadence == "daily":
        return base
    label = slot_label(cadence, slot)
    if label is None:
        return f"{base}, day not set yet"
    if cadence == "weekly":
        return f"every {label}" if n == 1 else f"{base} on {label}"
    return f"monthly on the {label}" if n == 1 else f"{base} on the {label}"


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
    if topic.get("slot") is not None and topic["cadence"] != "daily":
        lines.append(f"slot = {int(topic['slot'])}")
    if anchor_of(topic) is not None:
        lines.append(f"anchor = {anchor_of(topic)}")
    lines += [
        f"local = {'true' if topic.get('local', True) else 'false'}",
        f"active = {'true' if topic.get('active', True) else 'false'}",
        f"created = {topic.get('created') or today_local()}",
    ]
    if topic.get("extra_languages"):
        lines.append(f"extra_languages = {json.dumps(list(topic['extra_languages']))}")
    if every(topic) > 1:
        lines.append(f"every = {every(topic)}")
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
    if path.exists():
        with path.open("rb") as f:
            old = tomllib.load(f)
        topic = {**{k: old[k] for k in ("until", "every") if k in old}, **topic}
        same = old.get("cadence") == topic["cadence"] and every(old) == every(topic)
        if "anchor" not in topic and same and anchor_of(old) is not None:
            topic = with_anchor(topic, anchor_of(old))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_topic(topic), encoding="utf-8")
    return path


def assign_missing_slots(root: Path, today: date | None = None) -> list[str]:
    """Give every topic an anchor: its current next date if it has reports, else a balanced one."""
    today = today or today_local()
    changed = []
    for t in load_topics(root):
        if anchor_of(t) is not None:
            continue
        dates = report_dates(root, t["id"])
        if t["cadence"] != "daily" and t.get("slot") is None:
            t["slot"] = pick_slot(load_topics(root), t["cadence"])
        if dates:
            anchor = legacy_next_due(t, dates[-1])
        else:
            earliest = today + timedelta(days=min_gap(t))
            anchor = pick_anchor(root, load_topics(root), t, today, earliest)
        write_topic(root, with_anchor(t, anchor))
        changed.append(t["id"])
    return changed


def plan(root: Path, today: date, limit: int = MAX_PER_RUN) -> dict:
    assigned = assign_missing_slots(root, today)
    due = []
    for t in load_topics(root):
        if not is_due(root, t, today):
            continue
        dates = report_dates(root, t["id"])
        due.append(
            {
                "id": t["id"],
                "title": t["title"],
                "local": t["local"],
                "extra_languages": t["extra_languages"],
                "queued": is_queued(root, t["id"]),
                "previous_report": f"reports/{t['id']}/{dates[-1]}.md" if dates else None,
                "kind": "update" if dates else "baseline",
                "_order": (not is_queued(root, t["id"]), bool(dates), (dates or [date.min])[-1]),
            }
        )
    due.sort(key=lambda d: d.pop("_order"))
    return {
        "today": today.isoformat(),
        "read": load_reader(root)["read"],
        "max_local_languages": MAX_LOCAL,
        "reach_queue": len(reach_queue(root)),
        "slots_assigned": assigned,
        "run": due[:limit],
        "deferred": [d["id"] for d in due[limit:]],
    }


def done(root: Path, topic_id: str) -> None:
    (root / "queue" / topic_id).unlink(missing_ok=True)


SHOW_LIMITS = {"intro": 60, "outro": 40, "update": 280, "baseline": 350, "total": 1400}
QUIET_WORDS = (10, 15)
MAX_SENTENCE_WORDS = 40
SPOKEN_PUNCTUATION = set(".,;:!?'\"’‘“”—–-… ")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[\"”’]?\s+")


class ShowError(ValueError):
    pass


def read_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("+++"):
        return {}, text
    try:
        _, front, body = text.split("+++", 2)
        return tomllib.loads(front), body
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        raise ShowError(f"front matter: {exc}") from exc


def report_status(root: Path, topic_id: str, day: date) -> str | None:
    path = root / "reports" / topic_id / f"{day}.md"
    if not path.exists():
        return None
    try:
        meta, _ = read_front_matter(path.read_text(encoding="utf-8"))
    except ShowError:
        return "update"
    return meta.get("status", "update")


def reports_on(root: Path, day: date) -> dict[str, str]:
    found = {}
    for path in sorted((root / "reports").glob(f"*/{day}.md")):
        found[path.parent.name] = report_status(root, path.parent.name, day)
    return found


def parse_segment_heading(heading: str) -> dict:
    heading = heading.strip()
    if heading in ("intro", "outro"):
        return {"kind": heading, "topics": []}
    if heading.startswith("quiet:"):
        ids = [s.strip() for s in heading.removeprefix("quiet:").split(",") if s.strip()]
        return {"kind": "quiet", "topics": ids}
    return {"kind": "topic", "topics": [heading]}


def parse_show(text: str) -> dict:
    meta, body = read_front_matter(text)
    segments, preamble = [], []
    for line in body.splitlines():
        if line.startswith("## "):
            segments.append({**parse_segment_heading(line[3:]), "lines": []})
        elif segments:
            segments[-1]["lines"].append(line)
        elif line.strip():
            preamble.append(line)
    if preamble:
        raise ShowError("text before the first '## ' segment heading")
    for seg in segments:
        paragraphs = re.split(r"\n\s*\n", "\n".join(seg.pop("lines")).strip())
        seg["paragraphs"] = [" ".join(p.split()) for p in paragraphs if p.strip()]
    day = meta.get("date")
    return {"date": day if isinstance(day, date) else None, "segments": segments}


def load_show(path: Path) -> dict:
    return parse_show(path.read_text(encoding="utf-8"))


def words(text: str) -> int:
    return len(text.split())


def unspeakable(text: str) -> list[str]:
    bad = []
    for token in text.split():
        for ch in token:
            if ch in SPOKEN_PUNCTUATION:
                continue
            if ch.isalpha() and "LATIN" in unicodedata.name(ch, ""):
                continue
            bad.append(token)
            break
    return bad


def check_show(root: Path, path: Path) -> dict:
    problems: list[str] = []
    try:
        day = date.fromisoformat(path.stem)
    except ValueError:
        return {"ok": False, "problems": [f"file name must be shows/YYYY-MM-DD.md: {path.name}"]}
    if not path.exists():
        return {"ok": False, "problems": [f"no show at {path}"]}
    try:
        show = load_show(path)
    except ShowError as exc:
        return {"ok": False, "problems": [str(exc)]}
    if show["date"] != day:
        problems.append(f"front matter needs date = {day}")
    segments = show["segments"]
    kinds = [s["kind"] for s in segments]
    if not kinds or kinds[0] != "intro" or kinds.count("intro") != 1:
        problems.append("the show must open with exactly one '## intro'")
    if not kinds or kinds[-1] != "outro" or kinds.count("outro") != 1:
        problems.append("the show must end with exactly one '## outro'")
    if kinds.count("quiet") > 1:
        problems.append("put every quiet topic in a single '## quiet: a, b' segment")

    expected = reports_on(root, day)
    seen: list[str] = []
    for seg in segments:
        for topic_id in seg["topics"]:
            status = expected.get(topic_id)
            if status is None:
                problems.append(f"'{topic_id}' has no report dated {day}")
            elif seg["kind"] == "quiet" and status != "quiet":
                problems.append(f"'{topic_id}' is not quiet ({status}): give it its own segment")
            elif seg["kind"] == "topic" and status == "quiet":
                problems.append(f"'{topic_id}' is quiet: move it into the quiet segment")
            seen.append(topic_id)
    for topic_id in sorted(set(expected) - set(seen)):
        problems.append(f"today's report '{topic_id}' is missing from the show")
    for topic_id in sorted({t for t in seen if seen.count(t) > 1}):
        problems.append(f"'{topic_id}' appears more than once")

    total = 0
    for seg in segments:
        label = ", ".join(seg["topics"]) or seg["kind"]
        text = " ".join(seg["paragraphs"])
        n = words(text)
        total += n
        if not n:
            problems.append(f"[{label}] is empty")
            continue
        if seg["kind"] == "quiet":
            limit = QUIET_WORDS[0] + QUIET_WORDS[1] * len(seg["topics"])
        elif seg["kind"] == "topic":
            status = expected.get(seg["topics"][0])
            limit = SHOW_LIMITS["baseline" if status == "baseline" else "update"]
        else:
            limit = SHOW_LIMITS[seg["kind"]]
        if n > limit:
            problems.append(f"[{label}] has {n} words; the limit is {limit}")
        bad = unspeakable(text)
        if bad:
            shown = ", ".join(dict.fromkeys(bad[:8]))
            problems.append(f"[{label}] has characters that are not spoken words: {shown}")
        for paragraph in seg["paragraphs"]:
            if re.match(r"^([-*>•]|\d+[.)])\s", paragraph):
                problems.append(f"[{label}] has a list or quote line; write prose")
            for sentence in SENTENCE_SPLIT.split(paragraph):
                if words(sentence) > MAX_SENTENCE_WORDS:
                    problems.append(
                        f"[{label}] has a {words(sentence)}-word sentence; split it: "
                        f"{' '.join(sentence.split()[:8])}…"
                    )
    if total > SHOW_LIMITS["total"]:
        problems.append(f"the show has {total} words; the limit is {SHOW_LIMITS['total']}")
    return {"ok": not problems, "problems": problems, "words": total}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dispatch")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--today", type=date.fromisoformat, default=None)
    p_plan.add_argument("--max", type=int, default=MAX_PER_RUN)
    p_done = sub.add_parser("done")
    p_done.add_argument("topic_id")
    p_rplan = sub.add_parser("reach-plan")
    p_rplan.add_argument("--all", action="store_true")
    p_rec = sub.add_parser("reach-record")
    p_rec.add_argument("results", type=Path)
    p_rec.add_argument("--today", type=date.fromisoformat, default=None)
    p_reb = sub.add_parser("rebalance")
    p_reb.add_argument("--apply", action="store_true")
    p_reb.add_argument("--today", type=date.fromisoformat, default=None)
    p_fc = sub.add_parser("forecast")
    p_fc.add_argument("--days", type=int, default=14)
    p_fc.add_argument("--today", type=date.fromisoformat, default=None)
    p_show = sub.add_parser("check-show")
    p_show.add_argument("file", type=Path, nargs="?")
    args = parser.parse_args(argv)
    if args.cmd == "check-show":
        result = check_show(ROOT, args.file or ROOT / "shows" / f"{today_local()}.md")
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return 0 if result["ok"] else 1
    if args.cmd == "rebalance":
        start = (args.today or today_local()) + timedelta(days=1)
        result = {"moves": rebalance(ROOT, start, args.apply), "applied": args.apply}
    elif args.cmd == "forecast":
        start = args.today or today_local()
        result = {
            (start + timedelta(days=i)).isoformat(): ids
            for i, ids in enumerate(forecast(ROOT, start, args.days))
        }
    elif args.cmd == "plan":
        result = plan(ROOT, args.today or today_local(), args.max)
    elif args.cmd == "reach-plan":
        result = reach_plan(ROOT, args.all)
    elif args.cmd == "reach-record":
        changed = record_reach(ROOT, args.today or today_local(), read_reach_rows(args.results))
        result = {"changed": changed}
    else:
        done(ROOT, args.topic_id)
        return 0
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False, default=str)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
