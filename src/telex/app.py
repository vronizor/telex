import asyncio
import hashlib
import logging
import threading
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from telex import edition
from telex.config import get_settings
from telex.gitrepo import GitError, Repo
from telex.radio import library as radio
from telex.reads import Reads, is_new
from telex.store import Store, Topic, chat_prompt, full_text, pointer_prompt, report_path
from telex.wire import Wire, discover
from telex.wire import next_run as wire_next_run

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["static_version"] = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(STATIC_DIR.rglob("*.*")))
).hexdigest()[:10]
DISPATCH_PULLS = (15, 25, 40, 60)
ORDER = {"update": 0, "baseline": 1, "catchup": 2, "pending": 3, "quiet": 4, "paused": 5}
WINDOWS = ("the last three months", "the last six months", "the last year", "the last two years")
DEFAULT_WINDOW = "the last six months"
log = logging.getLogger("telex")


def short_date(day: date | None, today: date | None = None) -> str:
    if day is None:
        return ""
    if today is not None:
        delta = (day - today).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "tomorrow"
        if delta == -1:
            return "yesterday"
    return f"{day:%a} {day.day} {day:%b}"


templates.env.filters["short_date"] = short_date


settings = get_settings()
repo = Repo(settings.checkout, settings.repo_url, settings.git_name, settings.git_email)
store = Store(settings)
wire = Wire(settings.data_dir, store)
reads = Reads(settings.data_dir, store.now)


def refresh_edition() -> None:
    try:
        edition.ensure(store, settings)
    except Exception:
        log.exception("building the weekly edition failed")


def pull_and_build() -> None:
    repo.pull()
    refresh_edition()


def sync_delay(now: datetime, dispatch_time: time, minutes: int) -> float:
    candidates = [now + timedelta(minutes=minutes)]
    for day in (now.date(), now.date() + timedelta(days=1)):
        start = datetime.combine(day, dispatch_time, now.tzinfo)
        candidates += [start + timedelta(minutes=m) for m in DISPATCH_PULLS]
    return (min(c for c in candidates if c > now) - now).total_seconds()


async def sync_loop() -> None:
    while True:
        delay = sync_delay(store.now(), settings.dispatch_time, settings.sync_minutes)
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(pull_and_build)
        except Exception:
            log.exception("scheduled pull failed")


def wire_job(publish: bool) -> None:
    try:
        repo.pull()
        wire.pull(store.now())
        if publish:
            wire.publish(repo)
    except Exception:
        log.exception("wire job failed")


async def wire_loop() -> None:
    while True:
        when, publish = wire_next_run(store.now(), settings.dispatch_time, settings.feed_minutes)
        await asyncio.sleep(max(0.0, (when - store.now()).total_seconds()))
        await asyncio.to_thread(wire_job, publish)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(repo.ensure)
    await asyncio.to_thread(pull_and_build)
    tasks = [asyncio.create_task(sync_loop())]
    if settings.feed_minutes > 0:
        tasks.append(asyncio.create_task(wire_loop()))
    yield
    for task in tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Telex", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def same_origin_posts(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin:
        hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
        if urlsplit(origin).netloc not in hosts:
            return PlainTextResponse("Cross-site request refused", status_code=403)
    return await call_next(request)


def theme_name() -> str:
    name = settings.theme
    return (
        name if name.isidentifier() and (STATIC_DIR / "themes" / f"{name}.css").exists() else "wire"
    )


def unread_days(topic: Topic) -> list[date]:
    after = reads.report_read(topic.id)
    days = []
    for day in topic.report_days:
        if day <= after:
            continue
        latest = topic.latest
        report = latest if latest and day == latest.day else store.report(topic.id, day)
        if report is not None and report.status != "quiet":
            days.append(day)
    return days


def wire_new(topic: Topic) -> int:
    since = topic.latest.day if topic.latest else None
    seen = reads.wire_seen(topic.id)
    items, _ = wire.for_topic(topic.id, since, limit=None)
    return sum(is_new(i, seen) for i in items)


def show_mark(show: radio.Show | None) -> str | None:
    if show is None or show.state != "ready":
        return None
    state = reads.show(show.key if show.special else show.day)
    return None if state == "done" else "started" if state == "started" else "unread"


def radio_badge(show: radio.Show | None) -> dict:
    if show_mark(show) == "unread":
        return {"radio": True, "radio_day": show.key}
    special = next((s for s in specials() if show_mark(s) == "unread"), None)
    return {"radio": special is not None, "radio_day": special.key if special else None}


def badges(topics: list[Topic] | None = None, show: radio.Show | None = None) -> dict:
    topics = store.topics() if topics is None else topics
    show = home_show(store.now().date()) if show is None else show
    return {"reports": any(unread_days(t) for t in topics), **radio_badge(show)}


def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    now = store.now()
    if "badges" not in context:
        context["badges"] = badges()
    context.update(
        request=request, now=now, today=now.date(), repo=repo, settings=settings, theme=theme_name()
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    now = store.now()
    topics = store.topics()
    unread = {t.id for t in topics if unread_days(t)}
    on_wire = {t.id: n for t in topics if (n := wire_new(t))}
    fresh, earlier, quiet, waiting, caught_up = [], [], [], [], []
    for t in topics:
        if t.id in unread:
            fresh.append(t)
        elif t.catchup and t.state == "catchup":
            caught_up.append(t)
        elif t.state == "quiet":
            quiet.append(t)
        elif t.state in ("pending", "paused"):
            waiting.append(t)
        else:
            earlier.append(t)
    newest_first = lambda t: (t.latest.day if t.latest else date.min, t.title)  # noqa: E731
    for group in (fresh, earlier, quiet, caught_up):
        group.sort(key=newest_first, reverse=True)
    waiting.sort(key=lambda t: (ORDER[t.state], t.title))
    upcoming = sorted((t for t in topics if t.next_run), key=lambda t: (t.next_run, t.title))
    last_report = max((t.latest.day for t in topics if t.latest), default=None)
    show = home_show(now.date())
    fresh_specials = [s for s in specials() if show_mark(s) == "unread"]
    return render(
        request,
        "index.html",
        badges={"reports": bool(unread), **radio_badge(show)},
        fresh_specials=fresh_specials[:3],
        special_marks={s.key: show_mark(s) for s in fresh_specials},
        caught_up=caught_up,
        topics=topics,
        unread=unread,
        on_wire=on_wire,
        fresh=fresh,
        earlier=earlier,
        quiet=quiet,
        waiting=waiting,
        upcoming=upcoming[:8],
        week=store.week_ahead(store.next_dispatch(now)),
        last_report=last_report,
        next_dispatch=store.next_dispatch(now),
        latest_edition=next(iter(edition.list_editions(settings, limit=1)), None),
        show=show,
        show_mark=show_mark(show),
        chapters=chapter_links(show) if show else [],
        next_edition=edition.edition_day_for(now, settings) + timedelta(days=7),
        **form_context(None, NEW_TOPIC),
        **catchup_context(dict(NEW_CATCHUP)),
    )


@app.post("/read/all")
def mark_all_read():
    now = store.now()
    for t in store.topics():
        if t.latest:
            reads.mark_report(t.id, t.latest.day)
        reads.mark_wire(t.id, now)
    return RedirectResponse("/", status_code=303)


def topic_or_404(topic_id: str) -> Topic:
    topic = store.get(topic_id)
    if topic is None:
        raise HTTPException(404, "No such topic")
    return topic


def topic_view(request: Request, topic: Topic, report):
    history = [store.report(topic.id, day) for day in reversed(topic.report_days[-24:])]
    chat_url = copy_text = None
    if report is not None:
        gh = settings.github_repo
        sha = repo.last_commit(report_path(report)) if gh else None
        prompt = (
            pointer_prompt(topic.title, report, gh, sha)
            if gh and sha
            else chat_prompt(topic.title, report)
        )
        chat_url = "https://claude.ai/new?q=" + quote(prompt, safe="")
        copy_text = full_text(topic.title, report)
    was_unread = set(unread_days(topic))
    if report is not None:
        reads.mark_report(topic.id, report.day)
    wire_since = topic.latest.day if topic.latest else None
    wire_items, wire_total = wire.for_topic(topic.id, wire_since)
    seen = reads.wire_seen(topic.id)
    wire_fresh = {i["url"] for i in wire_items if is_new(i, seen)}
    reads.mark_wire(topic.id, store.now())
    special = None
    if report is not None and report.status == "catchup":
        special = radio.show_state(
            settings.checkout, settings.data_dir, f"{radio.SPECIALS}/{report.day}-{topic.id}"
        )
    return render(
        request,
        "topic.html",
        special=special,
        special_mark=show_mark(special),
        topic=topic,
        report=report,
        history=history,
        chat_url=chat_url,
        copy_text=copy_text,
        wire_items=wire_items,
        wire_total=wire_total,
        wire_since=wire_since,
        wire_fresh=wire_fresh,
        was_unread=was_unread,
    )


@app.get("/t/{topic_id}", response_class=HTMLResponse)
def topic_page(request: Request, topic_id: str):
    topic = topic_or_404(topic_id)
    return topic_view(request, topic, topic.latest)


@app.get("/t/{topic_id}/r/{day}", response_class=HTMLResponse)
def report_page(request: Request, topic_id: str, day: date):
    topic = topic_or_404(topic_id)
    report = store.report(topic_id, day)
    if report is None:
        raise HTTPException(404, "No report for that day")
    return topic_view(request, topic, report)


NEW_TOPIC = {
    "title": "",
    "brief": "",
    "cadence": "weekly",
    "every": 1,
    "until": None,
    "local": True,
    "extra_languages": [],
    "active": True,
    "next": None,
    "watch": [],
}


def describe_next(cadence: str, every: int, day: date | None, new: bool) -> str:
    d = store.d
    if day is None:
        return d.describe_schedule(cadence, None, every)
    slot = day.weekday() if cadence == "weekly" else day.day if cadence == "monthly" else None
    rhythm = d.describe_schedule(cadence, slot, every)
    if new:
        when = f"{day:%d %b}" if cadence == "weekly" else f"{day:%a %d %b}"
        return f"First report at the next run; then {rhythm} from {when}"
    return f"{rhythm[0].upper()}{rhythm[1:]}; next {day:%a %d %b}"


@app.get("/t/{topic_id}/follow", response_class=HTMLResponse)
def follow_topic(request: Request, topic_id: str):
    t = topic_or_404(topic_id)
    if not t.catchup:
        return RedirectResponse(f"/t/{topic_id}/edit", status_code=303)
    values = {
        **NEW_TOPIC,
        "title": t.title,
        "brief": t.brief,
        "local": t.local,
        "extra_languages": t.extra_languages,
        "watch": t.watch,
    }
    return form_page(request, t, values, follow=True)


def form_context(topic: Topic | None, values: dict, follow: bool = False) -> dict:
    d = store.d
    start = store.next_dispatch(store.now())
    suggestions = store.suggestions(start, exclude=topic.id if topic else None)
    until = values.get("until") if isinstance(values.get("until"), date) else None
    every = values["every"] if isinstance(values["every"], int) and values["every"] > 0 else 1
    chosen = values.get("next") if isinstance(values.get("next"), date) else None
    original = f"{topic.cadence}:{topic.every}" if topic and not follow else ""
    current = topic.next_run if topic and topic.next_run and not follow else None
    key = f"{values['cadence']}:{every}"
    if chosen:
        day = chosen
    elif topic and key == original:
        day = current
    else:
        day = date.fromisoformat(suggestions[key]) if key in suggestions else None
    preview = ""
    if values["cadence"] in d.CADENCES:
        preview = describe_next(values["cadence"], every, day, topic is None)
        preview += f", until {until.day} {until:%b}" if until else ""
    return {
        "topic": topic,
        "follow": follow,
        "values": values,
        "suggestions": suggestions,
        "original_key": original,
        "current_next": current.isoformat() if current else "",
        "next_value": chosen.isoformat() if chosen else "",
        "next_min": start.isoformat(),
        "preview": preview,
        "until_value": until.isoformat() if until else "",
        "max_every": d.MAX_EVERY,
        "languages": store.d.LANGUAGES,
        "cadences": store.d.CADENCES,
    }


def form_page(
    request: Request,
    topic: Topic | None,
    values: dict,
    error: str | None = None,
    follow: bool = False,
):
    return render(
        request,
        "form.html",
        error=error,
        status_code=400 if error else 200,
        **form_context(topic, values, follow),
    )


NEW_CATCHUP = {"title": "", "window": "", "brief": ""}


def catchup_context(values: dict) -> dict:
    return {"catchup_values": values, "windows": WINDOWS, "default_window": DEFAULT_WINDOW}


def catchup_page(request: Request, topic: Topic | None, values: dict, error: str | None = None):
    return render(
        request,
        "catchup.html",
        topic=topic,
        error=error,
        status_code=400 if error else 200,
        **catchup_context(values),
    )


async def read_catchup_form(request: Request) -> tuple[dict, str | None]:
    form = await request.form()
    values = {
        "title": " ".join(str(form.get("title", "")).split()),
        "window": " ".join(str(form.get("window", "")).split()),
        "brief": str(form.get("brief", "")).replace("\r\n", "\n").strip(),
    }
    error = None
    if not values["title"]:
        error = "Say what you want to catch up on."
    elif len(values["title"]) > 80:
        error = "Keep the subject under 80 characters."
    elif len(values["window"]) > 80:
        error = "Keep the period under 80 characters."
    elif len(values["brief"]) > 2000:
        error = "Keep the angle under 2000 characters."
    values["window"] = values["window"] or DEFAULT_WINDOW
    return values, error


def save_catchup(request: Request, topic: Topic | None, values: dict):
    previous = next(t for t in store.raw_topics() if t["id"] == topic.id) if topic else None
    values["id"] = topic.id if topic else store.unique_id(values["title"])
    try:
        path = store.save_catchup(values, previous)
        verb = "edit" if topic else "add"
        repo.commit_and_push([path], f"catchup: {verb} {values['id']}")
    except (GitError, ValueError) as exc:
        return catchup_page(request, topic, values, error=f"Could not save: {exc}")
    return RedirectResponse(f"/t/{values['id']}", status_code=303)


@app.get("/catchup", response_class=HTMLResponse)
def new_catchup(request: Request):
    return catchup_page(request, None, dict(NEW_CATCHUP))


@app.post("/catchup")
async def create_catchup(request: Request):
    values, error = await read_catchup_form(request)
    if error:
        return catchup_page(request, None, values, error)
    return save_catchup(request, None, values)


@app.get("/new", response_class=HTMLResponse)
def new_topic(request: Request):
    return form_page(request, None, dict(NEW_TOPIC))


@app.get("/t/{topic_id}/edit", response_class=HTMLResponse)
def edit_topic(request: Request, topic_id: str):
    t = topic_or_404(topic_id)
    if t.catchup:
        values = {"title": t.title, "window": t.window, "brief": t.brief}
        return catchup_page(request, t, values)
    values = {
        "title": t.title,
        "brief": t.brief,
        "cadence": t.cadence,
        "every": t.every,
        "until": t.until,
        "local": t.local,
        "extra_languages": t.extra_languages,
        "active": t.active,
        "next": None,
        "watch": t.watch,
    }
    return form_page(request, t, values)


def validate(values: dict) -> str | None:
    if not values["title"]:
        return "Give the topic a title."
    if len(values["title"]) > 80:
        return "Keep the title under 80 characters."
    if not values["brief"]:
        return "Say how this topic should be reported."
    if len(values["brief"]) > 2000:
        return "Keep the brief under 2000 characters."
    if values["cadence"] not in store.d.CADENCES:
        return "Pick a cadence."
    if values.get("until") == "invalid":
        return "Give the end date as a date, or leave it empty."
    if not 1 <= values["every"] <= store.d.MAX_EVERY:
        unit = {"daily": "days", "weekly": "weeks", "monthly": "months"}[values["cadence"]]
        return f"Repeat every 1 to {store.d.MAX_EVERY} {unit}."
    if values.get("next") == "invalid":
        return "Give the next report as a date, or leave it empty."
    if isinstance(values.get("next"), date):
        probe = {
            "cadence": values["cadence"],
            "every": values["every"],
            "until": values.get("until"),
        }
        start = store.next_dispatch(store.now())
        return store.d.anchor_error(probe, values["next"], start)
    return None


async def read_form(request: Request) -> dict:
    form = await request.form()
    extras = [lang for lang in form.getlist("extra_languages") if lang in store.d.LANGUAGES]
    try:
        every = int(form.get("every") or 1)
    except ValueError:
        every = 0
    raw_until = str(form.get("until") or "").strip()
    try:
        until = date.fromisoformat(raw_until) if raw_until else None
    except ValueError:
        until = "invalid"
    values = {
        "title": str(form.get("title", "")).strip(),
        "brief": str(form.get("brief", "")).replace("\r\n", "\n").strip(),
        "cadence": str(form.get("cadence", "")),
        "every": every,
        "local": form.get("local") is not None,
        "extra_languages": extras,
        "active": form.get("active") is not None,
        "watch": store.d.clean_watch(str(form.get("watch", "")).splitlines()),
    }
    if "until" in form:
        values["until"] = until
    raw_next = str(form.get("next") or "").strip()
    try:
        values["next"] = date.fromisoformat(raw_next) if raw_next else None
    except ValueError:
        values["next"] = "invalid"
    return values


def save(request: Request, topic: Topic | None, values: dict):
    previous = None
    if topic:
        previous = next(t for t in store.raw_topics() if t["id"] == topic.id)
        values["id"] = topic.id
    else:
        values["id"] = store.unique_id(values["title"])
        values["active"] = True
    try:
        path = store.save_topic(values, previous)
        verb = "edit" if topic else "add"
        repo.commit_and_push([path], f"topic: {verb} {values['id']}")
    except (GitError, ValueError) as exc:
        return form_page(request, topic, values, error=f"Could not save: {exc}")
    if values.get("watch") != (previous or {}).get("watch", []):
        threading.Thread(target=wire.rematch, args=(store.now().date(),), daemon=True).start()
    return RedirectResponse(f"/t/{values['id']}", status_code=303)


@app.post("/new")
async def create_topic(request: Request):
    values = await read_form(request)
    if error := validate(values):
        return form_page(request, None, values, error)
    return save(request, None, values)


@app.post("/t/{topic_id}/edit")
async def update_topic(request: Request, topic_id: str):
    topic = topic_or_404(topic_id)
    form = await request.form()
    follow = topic.catchup and form.get("follow") == "1"
    if topic.catchup and not follow:
        values, error = await read_catchup_form(request)
        if error:
            return catchup_page(request, topic, values, error)
        return save_catchup(request, topic, values)
    values = await read_form(request)
    if follow:
        values["catchup"] = False
        values["active"] = True
    if error := validate(values):
        return form_page(request, topic, values, error, follow=follow)
    return save(request, topic, values)


@app.post("/t/{topic_id}/refresh")
def refresh_topic(topic_id: str):
    topic = topic_or_404(topic_id)
    if not topic.queued:
        path = store.queue(topic_id)
        try:
            repo.commit_and_push([path], f"queue: {topic_id}")
        except GitError as exc:
            raise HTTPException(502, f"Could not queue the refresh: {exc}") from exc
    return RedirectResponse(f"/t/{topic_id}", status_code=303)


SOURCE_FILTERS = (
    ("cited", "Cited"),
    ("prefer", "Preferred"),
    ("voice", "Voices"),
    ("unreadable", "Unreadable"),
    ("feeds", "Feeds"),
    ("all", "All"),
)
TIER_ORDER = {"prefer": 0, None: 1, "avoid": 2}
REACH_MARKS = {"full": "●", "partial": "◐", "headline": "○", "none": "×"}


def outlet_order(o, show: str) -> tuple:
    if show == "cited":
        return (-o.cited, o.domain)
    return (TIER_ORDER.get(o.tier, 1), o.domain)


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


templates.env.globals["pct"] = pct


def feed_health(fs: dict, today: date) -> tuple[str, str]:
    if not fs.get("checked"):
        return "", "not fetched yet"
    if fs.get("error"):
        times = f" ×{fs['errors']}" if fs.get("errors", 0) > 1 else ""
        return "is-error", f"failing{times}: {fs['error']}"
    parts = []
    if ok := fs.get("ok"):
        when = datetime.fromisoformat(ok)
        parts.append(f"ok {when:%H:%M}" if when.date() == today else f"ok {when:%d.%m}")
    if counts := fs.get("new"):
        parts.append(f"~{round(sum(counts.values()) / len(counts))}/day")
    if (gap := fs.get("gap")) and datetime.fromisoformat(gap).date() > today - timedelta(days=7):
        parts.append("gaps this week")
    return "", " · ".join(parts)


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, show: str = "cited", find: str | None = None):
    if show not in dict(SOURCE_FILTERS):
        show = "cited"
    outlets, shares = store.sources()
    keep = {
        "cited": lambda o: o.cited > 0,
        "prefer": lambda o: o.tier == "prefer",
        "voice": lambda o: o.role == "voice",
        "unreadable": lambda o: o.reach in ("headline", "none") or o.unreachable > 0,
        "feeds": lambda o: bool(o.feeds),
        "all": lambda o: True,
    }[show]
    today = store.now().date()
    state = wire.state()
    health = {url: feed_health(state.get(url, {}), today) for o in outlets for url in o.feeds}
    found = found_error = None
    if find:
        try:
            found = discover(store.d.clean_domain(find))
        except ValueError as exc:
            found_error = f"Could not read the homepage: {exc}"
    shown = sorted(filter(keep, outlets), key=lambda o: outlet_order(o, show))
    counts = {
        "listed": sum(o.listed for o in outlets),
        "prefer": sum(o.tier == "prefer" for o in outlets),
        "voice": sum(o.role == "voice" for o in outlets),
        "cited": sum(o.cited > 0 for o in outlets),
    }
    return render(
        request,
        "sources.html",
        outlets=shown,
        shares=shares,
        counts=counts,
        show=show,
        filters=SOURCE_FILTERS,
        read=store.reader(),
        languages=store.d.LANGUAGES,
        max_tests=store.d.MAX_REACH_PER_RUN,
        marks=REACH_MARKS,
        health=health,
        find=find,
        found=found,
        found_error=found_error,
    )


@app.post("/sources/outlet")
async def update_outlet(request: Request):
    form = await request.form()
    domain = str(form.get("domain", ""))
    show = str(form.get("show", "cited"))
    changes = {k: (str(form[k]) or None) for k in ("tier", "role") if k in form}
    if not changes:
        raise HTTPException(400, "Nothing to change")
    try:
        path = store.set_outlet(domain, **changes)
        what = ", ".join(f"{k} {v or 'unset'}" for k, v in changes.items())
        repo.commit_and_push([path], f"sources: {domain} {what}")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except GitError as exc:
        raise HTTPException(502, f"Could not save: {exc}") from exc
    target = show if show in dict(SOURCE_FILTERS) else "cited"
    return RedirectResponse(f"/sources?show={target}#o-{quote(domain)}", status_code=303)


@app.post("/sources/feeds")
async def update_feeds(request: Request):
    form = await request.form()
    domain = str(form.get("domain", ""))
    url = str(form.get("url", "")).strip()
    action = str(form.get("action", "add"))
    show = str(form.get("show", "feeds"))
    try:
        if action == "remove":
            path = store.set_feeds(domain, remove=[url])
        else:
            path = store.set_feeds(domain, add=[url])
        repo.commit_and_push([path], f"sources: {domain} {action} feed")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except GitError as exc:
        raise HTTPException(502, f"Could not save: {exc}") from exc
    target = show if show in dict(SOURCE_FILTERS) else "feeds"
    return RedirectResponse(f"/sources?show={target}#o-{quote(domain)}", status_code=303)


@app.post("/sources/test")
async def test_outlet(request: Request):
    form = await request.form()
    show = str(form.get("show", "all"))
    try:
        domain = store.d.clean_domain(str(form.get("domain", "")))
        path = store.queue_reach(domain)
        repo.commit_and_push([path], f"reach: queue {domain}")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except GitError as exc:
        raise HTTPException(502, f"Could not queue the test: {exc}") from exc
    target = show if show in dict(SOURCE_FILTERS) else "all"
    return RedirectResponse(f"/sources?show={target}#o-{quote(domain)}", status_code=303)


@app.post("/sources/reader")
async def update_reader(request: Request):
    form = await request.form()
    path = store.set_reader([str(x) for x in form.getlist("read")])
    try:
        repo.commit_and_push([path], "reader: languages")
    except GitError as exc:
        raise HTTPException(502, f"Could not save: {exc}") from exc
    return RedirectResponse("/sources", status_code=303)


@app.post("/sync")
def sync(back: str = Form("/")):
    pull_and_build()
    target = back if back.startswith("/") and not back.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)


def radio_shows(today: date) -> list[radio.Show]:
    return radio.recent_shows(settings.checkout, settings.data_dir, today, settings.radio_keep_days)


def specials(topic: str | None = None) -> list[radio.Show]:
    return radio.specials(settings.checkout, settings.data_dir, topic)


@app.post("/radio/{day}/played")
async def radio_played(request: Request, day: date):
    form = await request.form()
    state = str(form.get("state", ""))
    if state not in ("started", "done"):
        raise HTTPException(400, "state must be started or done")
    if radio.show_state(settings.checkout, settings.data_dir, day) is None:
        raise HTTPException(404, "No show for that day")
    reads.mark_show(day, state)
    reads.forget_shows_before(store.now().date() - timedelta(days=settings.radio_keep_days + 7))
    return Response(status_code=204)


def special_or_404(stem: str) -> radio.Show:
    show = radio.show_state(settings.checkout, settings.data_dir, f"{radio.SPECIALS}/{stem}")
    if show is None:
        raise HTTPException(404, "No such special")
    return show


@app.post("/radio/specials/{stem}/played")
async def special_played(request: Request, stem: str):
    form = await request.form()
    state = str(form.get("state", ""))
    if state not in ("started", "done"):
        raise HTTPException(400, "state must be started or done")
    reads.mark_show(special_or_404(stem).key, state)
    return Response(status_code=204)


def home_show(today: date) -> radio.Show | None:
    shows = [s for s in radio_shows(today) if s.day >= today - timedelta(days=1)]
    return shows[0] if shows else None


def chapter_links(show: radio.Show) -> list[dict]:
    if show.special:
        return [
            {"start": c.start, "title": c.title, "kind": c.kind, "links": []} for c in show.chapters
        ]
    return [
        {
            "start": c.start,
            "title": c.title,
            "kind": c.kind,
            "links": [
                {"title": t.get("title") or t["id"], "href": f"/t/{t['id']}/r/{show.day}"}
                for t in c.topics
            ],
        }
        for c in show.chapters
    ]


def show_script(day: radio.Key) -> list[dict]:
    path = radio.script_path(settings.checkout, day)
    if not path.exists():
        return []
    try:
        load = store.d.load_special if radio.is_special(day) else store.d.load_show
        segments = load(path)["segments"]
    except (ValueError, AttributeError):
        return []
    if radio.is_special(day):
        names = {"intro": "Intro", "cast": "Who's who", "outro": "Outro"}
        for seg in segments:
            seg["title"] = seg["title"] or names.get(seg["kind"], seg["kind"])
        return segments
    titles = {t.id: t.title for t in store.topics()}
    for seg in segments:
        names = [titles.get(t, t) for t in seg["topics"]]
        seg["title"] = {"intro": "Intro", "outro": "Outro"}.get(seg["kind"]) or (
            ("Quiet: " if seg["kind"] == "quiet" else "") + ", ".join(names)
        )
    return segments


def radio_page(request: Request, day: date | None, special: radio.Show | None = None):
    today = store.now().date()
    shows = radio_shows(today)
    show = next((s for s in shows if s.day == day), None) if day else next(iter(shows), None)
    if day and show is None:
        raise HTTPException(404, "No show for that day")
    show = special or show
    extras = specials()
    marks = {s.key: show_mark(s) for s in [*shows, *extras]}
    titles = {t.id: t.title for t in store.topics()}
    return render(
        request,
        "radio.html",
        show=show,
        shows=shows,
        specials=extras,
        marks=marks,
        titles=titles,
        chapters=chapter_links(show) if show else [],
        script=show_script(show.key if show.special else show.day) if show else [],
    )


@app.get("/radio/specials/{stem}.mp3")
def special_audio(stem: str):
    show = special_or_404(stem)
    path = radio.audio_dir(settings.data_dir) / f"{show.key}.mp3"
    if not path.exists():
        raise HTTPException(404, "No audio for that special")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/radio/specials/{stem}", response_class=HTMLResponse)
def special_page(request: Request, stem: str):
    return radio_page(request, None, special_or_404(stem))


@app.get("/radio", response_class=HTMLResponse)
def radio_latest(request: Request):
    return radio_page(request, None)


@app.get("/radio/{day}.mp3")
def radio_audio(day: date):
    path = radio.audio_dir(settings.data_dir) / f"{day.isoformat()}.mp3"
    if not path.exists():
        raise HTTPException(404, "No audio for that day")
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/radio/{day}", response_class=HTMLResponse)
def radio_day(request: Request, day: date):
    return radio_page(request, day)


def public_base(request: Request) -> str:
    if settings.base_url:
        return settings.base_url
    forwarded = request.headers.get("x-forwarded-host")
    return f"https://{forwarded}" if forwarded else str(request.base_url).rstrip("/")


@app.get("/opds/")
def opds_feed(request: Request):
    editions = edition.list_editions(settings)
    body = templates.get_template("opds.xml").render(
        base=public_base(request),
        editions=editions,
        edition_name=edition.EDITION_NAME,
        updated=editions[0]["built_at"] if editions else store.now().isoformat(timespec="seconds"),
    )
    return Response(body, media_type="application/atom+xml;profile=opds-catalog;kind=acquisition")


def edition_file(day: date, suffix: str):
    path = edition.editions_dir(settings) / f"{day.isoformat()}{suffix}"
    if not path.exists():
        raise HTTPException(404, "No such edition")
    return path


@app.get("/opds/edition/{day}.epub")
def opds_epub(day: date):
    return FileResponse(
        edition_file(day, ".epub"),
        media_type="application/epub+zip",
        filename=f"telex-weekly-{day.isoformat()}.epub",
    )


@app.get("/opds/cover/{day}.png")
def opds_cover(day: date):
    return FileResponse(edition_file(day, ".png"), media_type="image/png")
