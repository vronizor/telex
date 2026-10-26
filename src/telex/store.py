import re
import tomllib
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from telex.checkout import dispatch
from telex.config import Settings

MARKS = {"+": "add", "~": "revised", "!": "contested", "-": "removed"}
MARK_RE = re.compile(r"^\s*[-*]\s+\[([+~!-])\]", re.MULTILINE)
DIFF_LI_RE = re.compile(r"<li>\[([+~!-])\]\s*(.*?)</li>", re.DOTALL)
TAG = r"(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{1,11}"
SOURCE_LINE_RE = re.compile(rf"^\s*[-*]\s+\[({TAG})\]\s+\[[^\]]*\]\((\S+?)\)", re.MULTILINE)
SOURCE_LI_RE = re.compile(rf"<li>\[({TAG})\]\s*(.*?)</li>", re.DOTALL)
TAG_RE = re.compile(rf"\[({TAG})\]")
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)
STORY = "story so far"
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


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
    body: str = ""
    story_html: str = ""
    searched: list[str] = field(default_factory=list)
    contested: str = ""
    unreachable: list[str] = field(default_factory=list)


def source_map(text: str) -> dict[str, str]:
    return {tag: url for tag, url in SOURCE_LINE_RE.findall(text)}


def split_sections(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Returns the text before the first '## ' heading and (heading, section text) pairs."""
    matches = list(SECTION_RE.finditer(body))
    if not matches:
        return body, []
    sections = []
    for m, nxt in zip(matches, matches[1:] + [None], strict=False):
        end = nxt.start() if nxt else len(body)
        sections.append((m.group(1).strip(), body[m.start() : end]))
    return body[: matches[0].start()], sections


def drop_heading(section: str) -> str:
    return section.split("\n", 1)[1] if "\n" in section else ""


def render_markdown(
    text: str, anchor_prefix: str | None = None, sources: dict[str, str] | None = None
) -> str:
    """Web mode links tags to the source URL; with anchor_prefix (EPUB) they link to endnotes."""
    sources = source_map(text) if sources is None else sources
    html = _md.render(text)
    epub = anchor_prefix is not None
    wrap = "span" if epub else "div"
    src_id = f"{anchor_prefix}-src" if epub else "src"

    def diff_item(m: re.Match) -> str:
        mark = m.group(1)
        symbol = "−" if mark == "-" else mark
        kind = MARKS[mark]
        flag = '<b class="d-flag">CONTESTED</b> — ' if epub and kind == "contested" else ""
        return (
            f'<li class="d d-{kind}"><span class="d-mark" title="{kind}">{symbol}</span>'
            f'<{wrap} class="d-text">{flag}{m.group(2)}</{wrap}></li>'
        )

    def source_item(m: re.Match) -> str:
        domain = ""
        if epub and (url := sources.get(m.group(1))):
            host = urlsplit(url).netloc.removeprefix("www.")
            domain = f' <span class="src-url">{escape(host)}</span>' if host else ""
        tag = m.group(1)
        return (
            f'<li class="src-row" id="{src_id}-{tag}"><span class="src-tag">{tag}</span>'
            f"<{wrap}>{m.group(2)}{domain}</{wrap}></li>"
        )

    def tag(m: re.Match) -> str:
        url = sources.get(m.group(1))
        if url is None or not url.startswith(("https://", "http://")):
            return f'<span class="src">[{m.group(1)}]</span>'
        href = f"#{src_id}-{m.group(1)}" if epub else escape(url, quote=True)
        return f'<a class="src" href="{href}">[{m.group(1)}]</a>'

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
    preamble, sections = split_sections(body)
    story = next((text for head, text in sections if head.lower() == STORY), "")
    rest = preamble + "".join(text for head, text in sections if head.lower() != STORY)
    all_sources = source_map(body)
    return Report(
        topic_id=topic_id,
        day=date.fromisoformat(path.stem),
        status=str(meta.get("status", "update")),
        headline=str(meta.get("headline", "")),
        since=since if isinstance(since, date) else None,
        html=render_markdown(rest, sources=all_sources),
        sources=len(all_sources),
        counts=counts,
        body=body,
        story_html=render_markdown(drop_heading(story), sources=all_sources) if story else "",
        searched=[str(x) for x in meta.get("searched", []) if isinstance(x, str)],
        contested=str(meta.get("contested", "")).strip(),
        unreachable=[str(x) for x in meta.get("unreachable", []) if isinstance(x, str)],
    )


@dataclass
class Topic:
    id: str
    title: str
    cadence: str
    slot: int | None
    local: bool
    extra_languages: list[str]
    active: bool
    brief: str
    schedule: str
    queued: bool
    report_days: list[date]
    latest: Report | None
    next_run: date | None
    every: int = 1
    until: date | None = None
    watch: list[str] = field(default_factory=list)

    @property
    def search_label(self) -> str:
        parts = (["local"] if self.local else []) + self.extra_languages
        return " + ".join(parts) or "en only"

    @property
    def state(self) -> str:
        if not self.active:
            return "paused"
        if self.latest is None:
            return "pending"
        return self.latest.status


@dataclass
class Outlet:
    domain: str
    info: dict
    cited: int = 0
    last_cited: date | None = None
    unreachable: int = 0
    topics: set[str] = field(default_factory=set)
    history: list[tuple[date, str]] = field(default_factory=list)
    queued: bool = False

    @property
    def listed(self) -> bool:
        return bool(self.info)

    @property
    def role(self) -> str | None:
        return self.info.get("role")

    @property
    def tier(self) -> str | None:
        return self.info.get("tier")

    @property
    def reach(self) -> str | None:
        return self.info.get("reach")

    @property
    def note(self) -> str:
        return self.info.get("note", "")

    @property
    def languages(self) -> list[str]:
        return list(self.info.get("languages", []))

    @property
    def feeds(self) -> list[str]:
        return list(self.info.get("feeds", []))


@dataclass
class WeekShare:
    start: date
    cited: int
    prefer: float
    voice: float
    avoid: float
    top3: float


def week_share(start: date, cited: list[str], outlets: dict[str, Outlet]) -> WeekShare:
    n = len(cited)
    tiers = Counter(outlets[d].tier for d in cited)
    voices = sum(1 for d in cited if outlets[d].role == "voice")
    top3 = sum(c for _, c in Counter(cited).most_common(3))
    return WeekShare(
        start=start,
        cited=n,
        prefer=tiers["prefer"] / n,
        voice=voices / n,
        avoid=tiers["avoid"] / n,
        top3=top3 / n,
    )


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().split(":")[0].removeprefix("www.")


def match_domain(host: str, known: dict) -> str:
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in known:
            return candidate
    return host


def read_front(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++"):
        return {}, text
    _, front, body = text.split("+++", 2)
    try:
        return tomllib.loads(front), body
    except tomllib.TOMLDecodeError:
        return {}, body


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
        until = raw.get("until") if isinstance(raw.get("until"), date) else None
        ended = until is not None and now.date() > until
        active = raw.get("active", True) and not ended
        if not active:
            next_run = None
        elif d.is_due(self.root, raw, now.date()):
            next_run = self.next_dispatch(now)
        else:
            next_run = d.next_due(raw, days[-1])
        return Topic(
            id=raw["id"],
            title=raw["title"],
            cadence=raw["cadence"],
            slot=raw.get("slot"),
            local=bool(raw.get("local", True)),
            extra_languages=list(raw.get("extra_languages", [])),
            active=active,
            brief=raw.get("brief", ""),
            schedule=d.describe_schedule(raw["cadence"], raw.get("slot"), d.every(raw))
            + (f", until {until.day} {until:%b}" if until else ""),
            queued=d.is_queued(self.root, raw["id"]),
            report_days=days,
            latest=latest,
            next_run=next_run,
            every=d.every(raw),
            until=until,
            watch=list(raw.get("watch", [])),
        )

    def topics(self) -> list[Topic]:
        now = self.now()
        return [self.topic(raw, now) for raw in self.raw_topics()]

    def get(self, topic_id: str) -> Topic | None:
        for raw in self.raw_topics():
            if raw["id"] == topic_id:
                return self.topic(raw)
        return None

    def week_ahead(self, start: date, days: int = 7) -> list[tuple[date, int]]:
        loads = self.d.forecast(self.root, start, days)
        return [(start + timedelta(days=i), len(ids)) for i, ids in enumerate(loads)]

    def suggestions(self, start: date, exclude: str | None = None) -> dict[str, str]:
        d = self.d
        topics = self.raw_topics()
        others = [t for t in topics if t["id"] != exclude]
        own = next((t for t in topics if t["id"] == exclude), None)
        dates = d.report_dates(self.root, exclude) if own else []
        base = [len(ids) for ids in d.forecast(self.root, start, 400, others)]
        out = {}
        for cadence in d.CADENCES:
            for n in range(1, d.MAX_EVERY + 1):
                probe = {"id": exclude or "", "cadence": cadence, "every": n}
                if own and isinstance(own.get("until"), date):
                    probe["until"] = own["until"]
                horizon = min(400, max(84, 2 * d.period_days(cadence, n) + 31))
                gap = timedelta(days=d.min_gap(probe))
                earliest = max(start, dates[-1] + gap) if dates else start + gap
                anchor = d.pick_anchor(self.root, others, probe, start, earliest, base[:horizon])
                out[f"{cadence}:{n}"] = anchor.isoformat()
        return out

    def unique_id(self, title: str) -> str:
        base = slugify(title)
        existing = {t["id"] for t in self.raw_topics()}
        candidate, n = base, 2
        while candidate in existing:
            candidate, n = f"{base[:56]}-{n}", n + 1
        return candidate

    def save_topic(self, data: dict, previous: dict | None) -> str:
        d = self.d
        if previous and previous.get("until") and "until" not in data:
            data["until"] = previous["until"]
        others = [t for t in self.raw_topics() if t["id"] != data["id"]]
        data["created"] = previous.get("created") if previous else self.now().date()
        same = (
            previous is not None
            and previous["cadence"] == data["cadence"]
            and d.every(previous) == d.every(data)
        )
        anchor = data.pop("next", None)
        if anchor is None and same:
            data["slot"] = previous.get("slot")
        else:
            if anchor is None:
                start = self.next_dispatch(self.now())
                gap = timedelta(days=d.min_gap(data))
                dates = d.report_dates(self.root, data["id"]) if previous else []
                earliest = max(start, dates[-1] + gap) if dates else start + gap
                anchor = d.pick_anchor(self.root, others, data, start, earliest)
            data = d.with_anchor(data, anchor)
        path = d.write_topic(self.root, data)
        return str(path.relative_to(self.root))

    def sources(self, weeks: int = 6) -> tuple[list[Outlet], list[WeekShare]]:
        known = self.d.load_sources(self.root)
        outlets = {domain: Outlet(domain, info) for domain, info in known.items()}
        by_week: dict[date, list[str]] = {}
        for raw in self.raw_topics():
            for day in self.d.report_dates(self.root, raw["id"]):
                meta, body = read_front(self.root / "reports" / raw["id"] / f"{day}.md")
                for url in source_map(body).values():
                    domain = match_domain(host_of(url), known)
                    if not domain:
                        continue
                    o = outlets.setdefault(domain, Outlet(domain, {}))
                    o.cited += 1
                    o.topics.add(raw["id"])
                    o.last_cited = max(o.last_cited or day, day)
                    by_week.setdefault(day - timedelta(days=day.weekday()), []).append(domain)
                for host in meta.get("unreachable", []):
                    if isinstance(host, str) and host.strip():
                        domain = match_domain(host.lower().removeprefix("www."), known)
                        outlets.setdefault(domain, Outlet(domain, {})).unreachable += 1
        for domain in self.d.reach_queue(self.root):
            outlets.setdefault(domain, Outlet(domain, {})).queued = True
        for domain, checks in self.d.reach_history(self.root).items():
            if domain in outlets:
                outlets[domain].history = checks[-8:]
        shares = [
            week_share(start, by_week[start], outlets)
            for start in sorted(by_week, reverse=True)[:weeks]
        ]
        return list(outlets.values()), shares

    def set_outlet(self, domain: str, **changes) -> str:
        return str(self.d.set_outlet(self.root, domain, **changes).relative_to(self.root))

    def set_feeds(self, domain: str, add: list[str] = (), remove: list[str] = ()) -> str:
        path = self.d.set_feeds(self.root, domain, add=add, remove=remove)
        return str(path.relative_to(self.root))

    def queue_reach(self, domain: str) -> str:
        return str(self.d.queue_reach(self.root, domain).relative_to(self.root))

    def reader(self) -> list[str]:
        return self.d.load_reader(self.root)["read"]

    def set_reader(self, read: list[str]) -> str:
        return str(self.d.write_reader(self.root, read).relative_to(self.root))

    def queue(self, topic_id: str) -> str:
        path = self.root / "queue" / topic_id
        path.parent.mkdir(exist_ok=True)
        path.touch()
        return str(path.relative_to(self.root))


CHAT_LIMIT = 1800
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)\)")


def _plain(markdown: str) -> str:
    text = MD_LINK_RE.sub(r"\1", markdown)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    cut = text[: max(0, budget - 2)]
    stop = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[: stop + 1] if stop > budget // 2 else cut).rstrip() + " …"


def chat_prompt(title: str, report: Report, limit: int = CHAT_LIMIT) -> str:
    """Context for a claude.ai/new?q= link; prefilled URLs get unreliable past ~2000 chars."""
    _, sections = split_sections(report.body)
    by_name = {head.lower(): _plain(drop_heading(text)) for head, text in sections}
    head = f'I\'m reading my Telex briefing on "{title}" ({report.day:%d %b %Y}): {report.headline}'
    ask = (
        "Use these sources and fresh searches to answer my follow-up questions. "
        "To start, suggest three questions worth digging into."
    )
    cited = TAG_RE.findall(report.body)
    sources = source_map(report.body)
    lines, used = [], set()
    for tag in [*cited, *sources]:
        if tag in sources and tag not in used:
            used.add(tag)
            lines.append(f"[{tag}] {sources[tag]}")
    src_block = ""
    for line in lines:
        candidate = f"{src_block}\n{line}" if src_block else line
        if len(candidate) > limit // 3:
            break
        src_block = candidate
    fixed = len(head) + len(ask) + len(src_block) + 40
    body_parts = [
        (name.capitalize() if name != STORY else "Story so far", by_name[name])
        for name in ("where it stands", "what changed", STORY, "background")
        if by_name.get(name)
    ]
    body = "\n\n".join(f"{name}:\n{text}" for name, text in body_parts) or _plain(report.body)
    body = _clip(body, max(200, limit - fixed))
    parts = [head, body]
    if src_block:
        parts.append(f"Sources:\n{src_block}")
    parts.append(ask)
    return "\n\n".join(parts)[:limit]


def report_path(report: Report) -> str:
    return f"reports/{report.topic_id}/{report.day.isoformat()}.md"


def pointer_prompt(title: str, report: Report, github_repo: str, sha: str) -> str:
    folder = f"reports/{report.topic_id}/"
    return (
        f'I\'m reading my Telex briefing on "{title}" ({report.day:%d %b %Y}).\n\n'
        f"Context: GitHub repo {github_repo} at commit {sha}, file {report_path(report)}. "
        f"Read that report first. Open earlier reports in {folder} only if my question "
        "needs the history (e.g. how the story drifted). Use its sources and fresh "
        "searches as needed.\n\n"
        "If there's no question below, just confirm you've read it and wait for mine."
    )


def full_text(title: str, report: Report) -> str:
    return f"# {title} — {report.day.isoformat()}\n\n{report.headline}\n\n{report.body.strip()}\n"


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:60].rstrip("-") or "topic"
