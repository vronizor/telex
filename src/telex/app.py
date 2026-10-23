import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta
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
from telex.store import Store, Topic, chat_prompt, full_text, pointer_prompt, report_path

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["static_version"] = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(STATIC_DIR.rglob("*.*")))
).hexdigest()[:10]
SEEN_COOKIE = "telex_seen"
SEEN_GRACE = timedelta(hours=1)
ORDER = {"update": 0, "baseline": 1, "pending": 2, "quiet": 3, "paused": 4}
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


def refresh_edition() -> None:
    try:
        edition.ensure(store, settings)
    except Exception:
        log.exception("building the weekly edition failed")


def pull_and_build() -> None:
    repo.pull()
    refresh_edition()


async def sync_loop() -> None:
    while True:
        await asyncio.sleep(settings.sync_minutes * 60)
        await asyncio.to_thread(pull_and_build)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(repo.ensure)
    await asyncio.to_thread(pull_and_build)
    task = asyncio.create_task(sync_loop())
    yield
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


def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    now = store.now()
    context.update(
        request=request, now=now, today=now.date(), repo=repo, settings=settings, theme=theme_name()
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def read_seen(request: Request, now: datetime) -> tuple[datetime | None, str]:
    since, last = None, None
    raw = request.cookies.get(SEEN_COOKIE, "")
    with suppress(ValueError):
        a, b = raw.split("|")
        since, last = datetime.fromisoformat(a), datetime.fromisoformat(b)
    if last is None:
        return None, f"{now.isoformat()}|{now.isoformat()}"
    if now - last > SEEN_GRACE:
        since = last
    return since, f"{since.isoformat()}|{now.isoformat()}"


def report_time(day: date) -> datetime:
    return datetime.combine(day, settings.dispatch_time, settings.timezone)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    now = store.now()
    since, cookie = read_seen(request, now)
    topics = store.topics()
    fresh, earlier, quiet, waiting = [], [], [], []
    for t in topics:
        if t.state in ("quiet",):
            quiet.append(t)
        elif t.state in ("pending", "paused"):
            waiting.append(t)
        elif since is not None and report_time(t.latest.day) > since:
            fresh.append(t)
        else:
            earlier.append(t)
    newest_first = lambda t: (t.latest.day if t.latest else date.min, t.title)  # noqa: E731
    for group in (fresh, earlier, quiet):
        group.sort(key=newest_first, reverse=True)
    waiting.sort(key=lambda t: (ORDER[t.state], t.title))
    upcoming = sorted((t for t in topics if t.next_run), key=lambda t: (t.next_run, t.title))
    daily = sum(1 for t in topics if t.active and t.cadence == "daily")
    monthly_days = sorted(
        {t.slot for t in topics if t.active and t.cadence == "monthly" and t.slot}
    )
    last_report = max((t.latest.day for t in topics if t.latest), default=None)
    response = render(
        request,
        "index.html",
        topics=topics,
        since=since,
        fresh=fresh,
        earlier=earlier,
        quiet=quiet,
        waiting=waiting,
        upcoming=upcoming[:8],
        weekly_loads=store.weekly_loads(),
        daily=daily,
        monthly_days=monthly_days,
        last_report=last_report,
        next_dispatch=store.next_dispatch(now),
        latest_edition=next(iter(edition.list_editions(settings, limit=1)), None),
        next_edition=edition.edition_day_for(now, settings) + timedelta(days=7),
        **form_context(None, NEW_TOPIC),
    )
    response.set_cookie(SEEN_COOKIE, cookie, max_age=400 * 86400, samesite="lax", httponly=True)
    return response


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
    return render(
        request,
        "topic.html",
        topic=topic,
        report=report,
        history=history,
        chat_url=chat_url,
        copy_text=copy_text,
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
}


def form_context(topic: Topic | None, values: dict) -> dict:
    d = store.d
    slots = store.new_slots(exclude=topic.id if topic else None)
    keep = topic is not None and topic.cadence == values["cadence"]
    slot = topic.slot if keep else slots.get(values["cadence"])
    prefix = "" if topic else "First report at the next run, then "
    until = values.get("until") if isinstance(values.get("until"), date) else None
    every = values["every"] if isinstance(values["every"], int) and values["every"] > 0 else 1
    return {
        "topic": topic,
        "values": values,
        "slot_labels": {c: d.slot_label(c, s) for c, s in slots.items()},
        "original_slot": d.slot_label(topic.cadence, topic.slot) if topic else None,
        "preview": prefix
        + d.describe_schedule(values["cadence"], slot, every)
        + (f", until {until.day} {until:%b}" if until else "")
        if values["cadence"] in d.CADENCES
        else "",
        "until_value": until.isoformat() if until else "",
        "preview_prefix": prefix,
        "max_every": d.MAX_EVERY,
        "languages": store.d.LANGUAGES,
        "cadences": store.d.CADENCES,
    }


def form_page(request: Request, topic: Topic | None, values: dict, error: str | None = None):
    return render(
        request,
        "form.html",
        error=error,
        status_code=400 if error else 200,
        **form_context(topic, values),
    )


@app.get("/new", response_class=HTMLResponse)
def new_topic(request: Request):
    return form_page(request, None, dict(NEW_TOPIC))


@app.get("/t/{topic_id}/edit", response_class=HTMLResponse)
def edit_topic(request: Request, topic_id: str):
    t = topic_or_404(topic_id)
    values = {
        "title": t.title,
        "brief": t.brief,
        "cadence": t.cadence,
        "every": t.every,
        "until": t.until,
        "local": t.local,
        "extra_languages": t.extra_languages,
        "active": t.active,
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
    }
    if "until" in form:
        values["until"] = until
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
    values = await read_form(request)
    if error := validate(values):
        return form_page(request, topic, values, error)
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


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, show: str = "cited"):
    if show not in dict(SOURCE_FILTERS):
        show = "cited"
    outlets, shares = store.sources()
    keep = {
        "cited": lambda o: o.cited > 0,
        "prefer": lambda o: o.tier == "prefer",
        "voice": lambda o: o.role == "voice",
        "unreadable": lambda o: o.reach in ("headline", "none") or o.unreachable > 0,
        "all": lambda o: True,
    }[show]
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
