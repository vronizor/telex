import importlib.util
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from html import escape
from pathlib import Path
from types import ModuleType

from markdown_it import MarkdownIt

from briefings.config import Settings

MARKS = {"+": "add", "~": "revised", "!": "contested", "-": "removed"}
MARK_RE = re.compile(r"^\s*[-*]\s+\[([+~!-])\]", re.MULTILINE)
DIFF_LI_RE = re.compile(r"<li>\[([+~!-])\]\s*(.*?)</li>", re.DOTALL)
TAG = r"[A-Z][A-Z0-9-]{1,11}"
SOURCE_LINE_RE = re.compile(rf"^\s*[-*]\s+\[({TAG})\]\s+\[[^\]]*\]\((\S+?)\)", re.MULTILINE)
SOURCE_LI_RE = re.compile(rf"<li>\[({TAG})\]\s*(.*?)</li>", re.DOTALL)
TAG_RE = re.compile(rf"\[({TAG})\]")
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
_dispatch_cache: dict[Path, tuple[float, ModuleType]] = {}


def dispatch(root: Path) -> ModuleType:
    path = root / "scripts" / "dispatch.py"
    mtime = path.stat().st_mtime
    cached = _dispatch_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    spec = importlib.util.spec_from_file_location("briefings_dispatch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _dispatch_cache[path] = (mtime, module)
    return module


@dataclass
class Report:
    topic_id: str
    day: date
    status: str
    headline: str
    since: date | None
    html: str
    counts: dict[str, int] = field(default_factory=dict)
    sources: int = 0


def source_map(text: str) -> dict[str, str]:
    return {tag: url for tag, url in SOURCE_LINE_RE.findall(text)}


def render_markdown(text: str) -> str:
    sources = source_map(text)
    html = _md.render(text)

    def diff_item(m: re.Match) -> str:
        mark = m.group(1)
        symbol = "−" if mark == "-" else mark
        kind = MARKS[mark]
        return (
            f'<li class="d d-{kind}"><span class="d-mark" title="{kind}">{symbol}</span>'
            f'<div class="d-text">{m.group(2)}</div></li>'
        )

    def source_item(m: re.Match) -> str:
        return (
            f'<li class="src-row" id="src-{m.group(1)}"><span class="src-tag">{m.group(1)}</span>'
            f"<div>{m.group(2)}</div></li>"
        )

    def tag(m: re.Match) -> str:
        url = sources.get(m.group(1))
        if url is None or not url.startswith(("https://", "http://")):
            return f'<span class="src">[{m.group(1)}]</span>'
        return f'<a class="src" href="{escape(url, quote=True)}">[{m.group(1)}]</a>'

    html = DIFF_LI_RE.sub(diff_item, html)
    html = SOURCE_LI_RE.sub(source_item, html)
    parts = re.split(r"(<[^>]+>)", html)
    return "".join(p if p.startswith("<") else TAG_RE.sub(tag, p) for p in parts)


def parse_report(path: Path, topic_id: str) -> Report:
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    body = text
    if text.startswith("+++"):
        _, front, body = text.split("+++", 2)
        try:
            meta = tomllib.loads(front)
        except tomllib.TOMLDecodeError:
            meta = {}
    counts = {kind: 0 for kind in MARKS.values()}
    for mark in MARK_RE.findall(body):
        counts[MARKS[mark]] += 1
    since = meta.get("since")
    return Report(
        topic_id=topic_id,
        day=date.fromisoformat(path.stem),
        status=str(meta.get("status", "update")),
        headline=str(meta.get("headline", "")),
        since=since if isinstance(since, date) else None,
        html=render_markdown(body),
        sources=len(source_map(body)),
        counts=counts,
    )


@dataclass
class Topic:
    id: str
    title: str
    cadence: str
    slot: int | None
    languages: list[str]
    active: bool
    brief: str
    schedule: str
    queued: bool
    report_days: list[date]
    latest: Report | None
    next_run: date | None

    @property
    def state(self) -> str:
        if not self.active:
            return "paused"
        if self.latest is None:
            return "pending"
        return self.latest.status


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.checkout

    @property
    def d(self) -> ModuleType:
        return dispatch(self.root)

    def now(self) -> datetime:
        return datetime.now(self.settings.timezone)

    def next_dispatch(self, now: datetime) -> date:
        today = now.date()
        return (
            today
            if now.time() < self.settings.dispatch_time
            else date.fromordinal(today.toordinal() + 1)
        )

    def raw_topics(self) -> list[dict]:
        return self.d.load_topics(self.root)

    def report(self, topic_id: str, day: date) -> Report | None:
        path = self.root / "reports" / topic_id / f"{day.isoformat()}.md"
        return parse_report(path, topic_id) if path.exists() else None

    def topic(self, raw: dict, now: datetime | None = None) -> Topic:
        d, now = self.d, now or self.now()
        days = d.report_dates(self.root, raw["id"])
        latest = self.report(raw["id"], days[-1]) if days else None
        active = raw.get("active", True)
        if not active:
            next_run = None
        elif d.is_due(self.root, raw, now.date()):
            next_run = self.next_dispatch(now)
        else:
            next_run = d.next_scheduled_after(raw, now.date())
        return Topic(
            id=raw["id"],
            title=raw["title"],
            cadence=raw["cadence"],
            slot=raw.get("slot"),
            languages=list(raw.get("languages", [])),
            active=active,
            brief=raw.get("brief", ""),
            schedule=d.describe_schedule(raw["cadence"], raw.get("slot")),
            queued=d.is_queued(self.root, raw["id"]),
            report_days=days,
            latest=latest,
            next_run=next_run,
        )

    def topics(self) -> list[Topic]:
        now = self.now()
        return [self.topic(raw, now) for raw in self.raw_topics()]

    def get(self, topic_id: str) -> Topic | None:
        for raw in self.raw_topics():
            if raw["id"] == topic_id:
                return self.topic(raw)
        return None

    def weekly_loads(self) -> list[int]:
        return self.d.slot_loads(self.raw_topics(), "weekly")

    def slot_previews(self, exclude: str | None = None) -> dict[str, str]:
        others = [t for t in self.raw_topics() if t["id"] != exclude]
        return {
            cadence: self.d.describe_schedule(cadence, self.d.pick_slot(others, cadence))
            for cadence in self.d.CADENCES
        }

    def unique_id(self, title: str) -> str:
        base = slugify(title)
        existing = {t["id"] for t in self.raw_topics()}
        candidate, n = base, 2
        while candidate in existing:
            candidate, n = f"{base[:56]}-{n}", n + 1
        return candidate

    def save_topic(self, data: dict, previous: dict | None) -> str:
        d = self.d
        others = [t for t in self.raw_topics() if t["id"] != data["id"]]
        if previous and previous["cadence"] == data["cadence"]:
            data["slot"] = previous.get("slot")
            data["created"] = previous.get("created")
        else:
            data["slot"] = d.pick_slot(others, data["cadence"])
            data["created"] = previous.get("created") if previous else self.now().date()
        path = d.write_topic(self.root, data)
        return str(path.relative_to(self.root))

    def queue(self, topic_id: str) -> str:
        path = self.root / "queue" / topic_id
        path.parent.mkdir(exist_ok=True)
        path.touch()
        return str(path.relative_to(self.root))


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:60].rstrip("-") or "topic"
