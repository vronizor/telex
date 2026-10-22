"""Weekly e-reader edition: one EPUB per week, rebuilt whenever the week's reports change."""

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
from pathlib import Path

from ebooklib import epub
from PIL import Image, ImageDraw, ImageFont

from telex.config import Settings, get_settings
from telex.store import (
    STORY,
    Report,
    Store,
    drop_heading,
    render_markdown,
    source_map,
    split_sections,
)

log = logging.getLogger(__name__)

FORMAT_VERSION = "4"
EDITION_NAME = "Telex Weekly"
FONTS = Path(__file__).parent / "static" / "fonts"
TAGS = {"update": "UPD", "baseline": "NEW", "quiet": "QUIET"}
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
LATEST_ONLY = {"where it stands", "watch next"}
STATE_ORDER = {"update": 0, "baseline": 1, "quiet": 2}
MAX_COVER_TOPICS = 6
ITA2 = {
    "A": "11000",
    "B": "10011",
    "C": "01110",
    "D": "10010",
    "E": "10000",
    "F": "10110",
    "G": "01011",
    "H": "00101",
    "I": "01100",
    "J": "11010",
    "K": "11110",
    "L": "01001",
    "M": "00111",
    "N": "00110",
    "O": "00011",
    "P": "01101",
    "Q": "11101",
    "R": "01010",
    "S": "10100",
    "T": "00001",
    "U": "11100",
    "V": "01111",
    "W": "11001",
    "X": "10111",
    "Y": "10101",
    "Z": "10001",
}

CSS = (Path(__file__).parent / "static" / "epub.css").read_text()


@dataclass
class TopicWeek:
    id: str
    title: str
    code: str
    reports: list[Report]

    @property
    def substantive(self) -> list[Report]:
        return [r for r in self.reports if r.status != "quiet"]

    @property
    def state(self) -> str:
        statuses = {r.status for r in self.reports}
        for state in ("update", "baseline"):
            if state in statuses:
                return state
        return "quiet"

    @property
    def latest(self) -> Report:
        return (self.substantive or self.reports)[-1]

    @property
    def counts(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for r in self.reports:
            for k, v in r.counts.items():
                total[k] = total.get(k, 0) + v
        return total


@dataclass
class Edition:
    day: date
    topics: list[TopicWeek]

    @property
    def start(self) -> date:
        return self.day - timedelta(days=6)

    @property
    def chaptered(self) -> list[TopicWeek]:
        return [t for t in self.topics if t.state != "quiet"]

    @property
    def quiet(self) -> list[TopicWeek]:
        return [t for t in self.topics if t.state == "quiet"]

    @property
    def week(self) -> int:
        return self.day.isocalendar().week

    @property
    def range_label(self) -> str:
        s, e = self.start, self.day
        if s.month == e.month:
            return f"{s.day}–{e.day} {e:%b %Y}"
        if s.year == e.year:
            return f"{s.day} {s:%b} – {e.day} {e:%b %Y}"
        return f"{s.day} {s:%b %Y} – {e.day} {e:%b %Y}"

    @property
    def title(self) -> str:
        return f"{EDITION_NAME} · {self.range_label}"

    @property
    def summary(self) -> str:
        n = {s: sum(1 for t in self.topics if t.state == s) for s in STATE_ORDER}
        parts = []
        if n["update"]:
            parts.append(f"{n['update']} update{'s' if n['update'] != 1 else ''}")
        if n["baseline"]:
            parts.append(f"{n['baseline']} new")
        if n["quiet"]:
            parts.append(f"{n['quiet']} quiet")
        return " · ".join(parts)

    def digest(self) -> str:
        payload = [FORMAT_VERSION, self.day.isoformat()] + [
            [
                t.id,
                t.title,
                t.code,
                [[r.day.isoformat(), r.status, r.headline, r.body] for r in t.reports],
            ]
            for t in self.topics
        ]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def counts_text(counts: dict[str, int]) -> str:
    symbols = (("add", "+"), ("revised", "~"), ("contested", "!"), ("removed", "−"))
    return " ".join(f"{sym}{counts[k]}" for k, sym in symbols if counts.get(k))


def schedule_code(raw: dict) -> str:
    cadence, slot = raw["cadence"], raw.get("slot")
    try:
        n = int(raw.get("every", 1))
    except (TypeError, ValueError):
        n = 1
    k = str(n) if n > 1 else ""
    if cadence == "daily":
        return f"{k}D" if k else "DAILY"
    if slot is None:
        return f"{k}WK·?" if cadence == "weekly" else f"{k}MO·?"
    return f"{k}WK·{DAYS[slot].upper()}" if cadence == "weekly" else f"{k}MO·{slot}"


def edition_day_for(now: datetime, settings: Settings) -> date:
    today = now.date()
    day = today - timedelta(days=(today.weekday() - settings.edition_weekday) % 7)
    if day == today and now.time() < settings.edition_time:
        day -= timedelta(days=7)
    return day


def collect(store: Store, day: date) -> Edition:
    start = day - timedelta(days=6)
    topics = []
    for raw in store.raw_topics():
        days = [d for d in store.d.report_dates(store.root, raw["id"]) if start <= d <= day]
        if not days:
            continue
        reports = [store.report(raw["id"], d) for d in days]
        topics.append(TopicWeek(raw["id"], raw["title"], schedule_code(raw), reports))
    topics.sort(key=lambda t: (STATE_ORDER[t.state], -t.latest.day.toordinal(), t.title))
    return Edition(day, topics)


def page(title: str, body: str) -> str:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en"><head>'
        f"<title>{escape(title)}</title>"
        '<link rel="stylesheet" href="style/style.css" type="text/css"/></head>'
        f"<body>{body}</body></html>"
    )


def report_body(report: Report, latest: bool) -> str:
    preamble, sections = split_sections(report.body)
    kept = [
        text
        for head, text in sections
        if head.lower() != STORY and (latest or head.lower() not in LATEST_ONLY)
    ]
    return preamble + "".join(kept)


def as_epub_html(markdown: str, prefix: str, sources: dict[str, str]) -> str:
    html = render_markdown(markdown, anchor_prefix=prefix, sources=sources)
    return html.replace("<h2>", '<h3 class="sec">── ').replace("</h2>", "</h3>")


def chapter(t: TopicWeek) -> str:
    latest = t.latest
    head = (
        f'<p class="label">{TAGS[t.state]} · {escape(t.title)} · {t.code}</p>'
        f"<h1>{escape(latest.headline or t.title)}</h1>"
    )
    substantive = t.substantive
    meta = [f"{len(substantive)} report{'s' if len(substantive) != 1 else ''} this week"]
    if counts_text(t.counts):
        meta.append(counts_text(t.counts))
    if substantive and substantive[0].since:
        meta.append(f"since {substantive[0].since:%d %b}")
    parts = [head, f'<p class="label">{" · ".join(meta)}</p>']

    _, sections = split_sections(latest.body)
    story = next((text for h, text in sections if h.lower() == STORY), "")
    latest_prefix = f"r{latest.day:%m%d}"
    if story:
        parts.append('<h2 class="sec">── Story so far</h2>')
        story_html = render_markdown(
            drop_heading(story), anchor_prefix=latest_prefix, sources=source_map(latest.body)
        )
        parts.append(f'<div class="story">{story_html}</div>')

    multi = len(substantive) > 1
    for r in t.reports:
        if r.status == "quiet":
            parts.append(f'<p class="label quiet-line">{r.day:%a %d %b} · quiet</p>')
            continue
        if multi:
            parts.append(f'<h2 class="day">{r.day:%a %d %b} — {escape(r.headline)}</h2>')
        prefix = f"r{r.day:%m%d}"
        parts.append(as_epub_html(report_body(r, r is latest), prefix, source_map(r.body)))
    return page(t.title, "".join(parts))


def contents(edition: Edition, chapter_files: list[str], next_day: date) -> str:
    parts = [
        f'<p class="label">{EDITION_NAME} · week {edition.week}</p>',
        f"<h1>{edition.range_label}</h1>",
        f'<p class="label">{escape(edition.summary)}</p>',
        '<h2 class="sec">── This week</h2>',
    ]
    for t, file in zip(edition.chaptered, chapter_files, strict=True):
        meta = " · ".join(x for x in (TAGS[t.state], t.code, counts_text(t.counts)) if x)
        parts.append(
            '<div class="toc-item">'
            f'<p class="label">{meta}</p>'
            f'<p class="toc-title"><a href="{file}">{escape(t.title)}</a></p>'
            f'<p class="toc-head">{escape(t.latest.headline)}</p>'
            "</div>"
        )
    if edition.quiet:
        parts.append('<h2 class="sec">── Quiet this week</h2>')
        for t in edition.quiet:
            parts.append(
                f'<p class="quiet-line"><b>{escape(t.title)}.</b> {escape(t.latest.headline)}</p>'
            )
    parts.append(f'<p class="label">Next edition {next_day:%a %d %b}</p>')
    return page("This week", "".join(parts))


def _font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def draw_tape(d: ImageDraw.ImageDraw, word: str, x0: int, top: int, pitch: int) -> None:
    """Punch `word` in ITA2: five data rows with the feed row between bits 3 and 4."""
    rows = [top + pitch * i for i in (1, 2, 3, 5, 6)]
    feed_y = top + pitch * 4
    r, feed_r = pitch * 0.38, pitch * 0.19
    for i, letter in enumerate(word.upper()):
        x = x0 + i * pitch * 2.5
        d.ellipse((x - feed_r, feed_y - feed_r, x + feed_r, feed_y + feed_r), fill=255)
        for bit, y in zip(ITA2.get(letter, "00000"), rows, strict=True):
            if bit == "1":
                d.ellipse((x - r, y - r, x + r, y + r), fill=255)


def make_cover(edition: Edition) -> bytes:
    w, h, m = 1072, 1448, 72
    img = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(img)
    mono = _font("ibm-plex-mono-latin-500-normal.woff2", 34)
    mono_small = _font("ibm-plex-mono-latin-400-normal.woff2", 30)
    sans = _font("ibm-plex-sans-latin-500-normal.woff2", 40)
    small = _font("ibm-plex-sans-latin-400-normal.woff2", 28)
    big = _font("ibm-plex-sans-latin-600-normal.woff2", 92)

    band = 176
    d.rectangle((0, 0, w, band), fill=150)
    draw_tape(d, "TELEX", m + 20, 4, 24)
    d.text((w - m, band // 2), f"WEEKLY · WEEK {edition.week}", font=mono, fill=0, anchor="rm")
    d.text((m, 270), edition.range_label, font=big, fill=0, anchor="lm")
    d.line((m, 370, w - m, 370), fill=0, width=3)

    y = 440
    tag_w = 150
    for t in edition.topics[:MAX_COVER_TOPICS]:
        tag = TAGS[t.state]
        box = (m, y - 26, m + tag_w - 24, y + 26)
        if t.state == "update":
            d.rectangle(box, fill=0)
            d.text(((box[0] + box[2]) // 2, y), tag, font=mono_small, fill=255, anchor="mm")
        else:
            d.rectangle(box, outline=0 if t.state == "baseline" else 140, width=3)
            d.text(
                ((box[0] + box[2]) // 2, y),
                tag,
                font=mono_small,
                fill=0 if t.state == "baseline" else 110,
                anchor="mm",
            )
        counts = counts_text(t.counts)
        counts_w = int(d.textlength(counts, font=mono_small)) if counts else 0
        title_w = w - 2 * m - tag_w - counts_w - 30
        colour = 0 if t.state != "quiet" else 110
        d.text((m + tag_w, y), _fit(d, t.title, sans, title_w), font=sans, fill=colour, anchor="lm")
        if counts:
            d.text((w - m, y), counts, font=mono_small, fill=0, anchor="rm")
        if t.state != "quiet" and t.latest.headline:
            headline = _fit(d, t.latest.headline, small, w - 2 * m - tag_w)
            d.text((m + tag_w, y + 46), headline, font=small, fill=70, anchor="lm")
            y += 128
        else:
            y += 92
    if len(edition.topics) > MAX_COVER_TOPICS:
        more = len(edition.topics) - MAX_COVER_TOPICS
        d.text((m, y), f"+ {more} more", font=mono_small, fill=90, anchor="lm")

    d.line((m, h - 150, w - m, h - 150), fill=0, width=3)
    d.text((m, h - 95), edition.summary.upper(), font=mono_small, fill=0, anchor="lm")
    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def editions_dir(settings: Settings) -> Path:
    return settings.data_dir / "editions"


def build(edition: Edition, settings: Settings) -> dict:
    out = editions_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    stem = edition.day.isoformat()
    next_day = edition.day + timedelta(days=7)

    book = epub.EpubBook()
    book.set_identifier(f"telex-weekly-{stem}")
    book.set_title(edition.title)
    book.set_language("en")
    book.add_author("Telex")
    cover = make_cover(edition)
    book.set_cover("cover.png", cover)
    book.add_item(
        epub.EpubItem(uid="style", file_name="style/style.css", media_type="text/css", content=CSS)
    )
    files = [f"topic_{i}.xhtml" for i in range(len(edition.chaptered))]
    toc_page = epub.EpubHtml(
        uid="contents", file_name="contents.xhtml", title="This week", lang="en"
    )
    toc_page.content = contents(edition, files, next_day)
    toc_page.add_link(href="style/style.css", rel="stylesheet", type="text/css")
    book.add_item(toc_page)
    chapters = []
    for t, file in zip(edition.chaptered, files, strict=True):
        ch = epub.EpubHtml(
            uid=file.removesuffix(".xhtml"), file_name=file, title=t.title, lang="en"
        )
        ch.content = chapter(t)
        ch.add_link(href="style/style.css", rel="stylesheet", type="text/css")
        book.add_item(ch)
        chapters.append(ch)
    book.toc = [toc_page, *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["cover", toc_page, *chapters]

    tmp = out / f".{stem}.epub.tmp"
    epub.write_epub(str(tmp), book)
    tmp.replace(out / f"{stem}.epub")
    (out / f"{stem}.png").write_bytes(cover)
    meta = {
        "day": stem,
        "start": edition.start.isoformat(),
        "title": edition.title,
        "range": edition.range_label,
        "summary": edition.summary,
        "digest": edition.digest(),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (out / f"{stem}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta


def ensure(store: Store, settings: Settings, day: date | None = None) -> dict | None:
    day = day or edition_day_for(store.now(), settings)
    edition = collect(store, day)
    if not edition.topics:
        return None
    meta_path = editions_dir(settings) / f"{day.isoformat()}.json"
    epub_path = meta_path.with_suffix(".epub")
    if meta_path.exists() and epub_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta.get("digest") == edition.digest():
            return meta
    meta = build(edition, settings)
    log.info("built edition %s (%s)", meta["day"], meta["summary"])
    return meta


def list_editions(settings: Settings, limit: int = 52) -> list[dict]:
    folder = editions_dir(settings)
    if not folder.exists():
        return []
    metas = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        if path.with_suffix(".epub").exists():
            metas.append(json.loads(path.read_text()))
    return metas[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="telex.edition")
    parser.add_argument("--date", type=date.fromisoformat, help="edition date (default: latest)")
    args = parser.parse_args(argv)
    settings = get_settings()
    meta = ensure(Store(settings), settings, args.date)
    print(json.dumps(meta, indent=2, ensure_ascii=False) if meta else "no reports in that week")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
