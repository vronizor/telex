"""Telex Wire: RSS/Atom feeds fetched on the Pi, kept locally, matched to topics' watch terms."""

import argparse
import json
import logging
import threading
import time as _time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

USER_AGENT = "Telex/0.1 (self-hosted personal feed reader)"
TIMEOUT = 15
MAX_BYTES = 5_000_000
SUMMARY_CHARS = 600
KEEP_DAYS = 45
SAME_HOST_PAUSE = 1.0
FETCH_OFFSET_MINUTES = 7
PUBLISH_BEFORE_DISPATCH = timedelta(minutes=15)
TOPIC_PAGE_LIMIT = 30
ATOM = "{http://www.w3.org/2005/Atom}"
BLOCK_TAGS = {"p", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "blockquote", "tr"}
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml")
log = logging.getLogger("telex.wire")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def plain(html: str | None) -> str:
    parser = _Text()
    parser.feed(html or "")
    parser.close()
    return " ".join("".join(parser.parts).split())


def clip(text: str, limit: int = SUMMARY_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:") + "…"


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el: ET.Element, *names: str) -> ET.Element | None:
    for child in el:
        if _local(child.tag) in names:
            return child
    return None


def _text(el: ET.Element, *names: str) -> str:
    child = _child(el, *names)
    return "".join(child.itertext()).strip() if child is not None else ""


def parse_date(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    if dt.tzinfo is None:
        return None
    return dt.isoformat(timespec="seconds")


def _atom_link(entry: ET.Element) -> str:
    links = [c for c in entry if _local(c.tag) == "link"]
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "")
    return links[0].get("href", "") if links else ""


def _item(title: str, url: str, guid: str, summary: str, published: str) -> dict:
    return {
        "id": guid.strip() or url.strip(),
        "title": clip(plain(title), 300),
        "url": url.strip(),
        "summary": clip(plain(summary)),
        "published": parse_date(published),
    }


def parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0, RSS 1.0/RDF and Atom. Keeps title, link, id, summary and date; never the body."""
    root = ET.fromstring(data.lstrip())
    items = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":
            link_el = _child(el, "link")
            link = _text(el, "link") or (link_el.get("href", "") if link_el is not None else "")
            items.append(
                _item(
                    _text(el, "title"),
                    link,
                    _text(el, "guid")
                    or el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about", ""),
                    _text(el, "description", "summary"),
                    _text(el, "pubDate", "date", "published", "updated"),
                )
            )
        elif name == "entry" and el.tag.startswith(ATOM):
            items.append(
                _item(
                    _text(el, "title"),
                    _atom_link(el),
                    _text(el, "id"),
                    _text(el, "summary"),
                    _text(el, "published", "updated"),
                )
            )
    return [i for i in items if i["title"] and i["url"].startswith(("https://", "http://"))]


@dataclass
class Fetched:
    status: int
    body: bytes | None = None
    etag: str | None = None
    modified: str | None = None
    error: str | None = None


def fetch(url: str, etag: str | None = None, modified: str | None = None) -> Fetched:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5",
    }
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return Fetched(response.status, error="larger than 5 MB")
            return Fetched(
                response.status,
                body,
                response.headers.get("ETag"),
                response.headers.get("Last-Modified"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return Fetched(304, etag=etag, modified=modified)
        return Fetched(exc.code, error=f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return Fetched(0, error=str(getattr(exc, "reason", exc))[:120])


def next_run(now: datetime, dispatch_time: time, minutes: int) -> tuple[datetime, bool]:
    """When the next job is due, and whether it also publishes to the data repo."""
    publish = None
    for day in (now.date(), now.date() + timedelta(days=1)):
        candidate = datetime.combine(day, dispatch_time, now.tzinfo) - PUBLISH_BEFORE_DISPATCH
        if candidate > now:
            publish = candidate
            break
    midnight = datetime.combine(now.date(), time(0), now.tzinfo)
    elapsed = (now - midnight).total_seconds() / 60 - FETCH_OFFSET_MINUTES
    slot = int(elapsed // minutes) + 1
    fetch_at = midnight + timedelta(minutes=FETCH_OFFSET_MINUTES + slot * minutes)
    if publish <= fetch_at:
        return publish, True
    return fetch_at, False


class _LinkFinder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        rels = a.get("rel", "").lower().split()
        if tag == "link" and "alternate" in rels and a.get("type", "").lower() in FEED_TYPES:
            self.found.append((a.get("title", "").strip(), a.get("href", "").strip()))


def discover(domain: str, fetcher=fetch) -> list[dict]:
    """Feeds a site advertises on its homepage with <link rel="alternate">."""
    base = domain if domain.startswith(("https://", "http://")) else f"https://{domain}/"
    result = fetcher(base)
    if result.error or not result.body:
        raise ValueError(result.error or f"HTTP {result.status}")
    finder = _LinkFinder()
    finder.feed(result.body.decode("utf-8", errors="replace"))
    out, seen = [], set()
    for title, href in finder.found:
        url = urljoin(base, href)
        if url.startswith(("https://", "http://")) and url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out


def check(url: str, fetcher=fetch) -> dict:
    result = fetcher(url)
    row = {"url": url, "status": result.status, "error": result.error}
    if result.error or not result.body:
        row["error"] = row["error"] or "empty response"
        return row
    try:
        items = parse_feed(result.body)
    except ET.ParseError as exc:
        row["error"] = f"not a feed: {exc}"[:120]
        return row
    dates = sorted(i["published"] for i in items if i["published"])
    row.update(
        items=len(items),
        newest=dates[-1] if dates else None,
        with_summary=sum(1 for i in items if i["summary"]),
    )
    return row


class Wire:
    def __init__(self, data_dir: Path, store, fetcher=fetch, sleep=_time.sleep):
        self.base = data_dir
        self.dir = data_dir / "wire"
        self.store = store
        self.fetcher = fetcher
        self.sleep = sleep
        self.lock = threading.Lock()
        self._keys: dict[tuple[str, str], str] | None = None

    @property
    def items_dir(self) -> Path:
        return self.dir / "items"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    def _day_files(self, start: date) -> list[Path]:
        files = []
        for path in sorted(self.items_dir.glob("*.jsonl")):
            try:
                if date.fromisoformat(path.stem) >= start:
                    files.append(path)
            except ValueError:
                continue
        return files

    def load_items(self, start: date) -> list[dict]:
        items = []
        for path in self._day_files(start):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return items

    def _known(self, today: date) -> dict[tuple[str, str], str]:
        if self._keys is None:
            self._keys = {
                (i.get("domain", ""), i.get("id", "")): str(i.get("seen", ""))[:10]
                for i in self.load_items(today - timedelta(days=KEEP_DAYS))
            }
        return self._keys

    def _prune(self, today: date) -> None:
        cutoff = today - timedelta(days=KEEP_DAYS)
        for path in self.items_dir.glob("*.jsonl"):
            try:
                if date.fromisoformat(path.stem) < cutoff:
                    path.unlink()
            except ValueError:
                continue
        if self._keys is not None:
            floor = cutoff.isoformat()
            self._keys = {k: v for k, v in self._keys.items() if v >= floor}

    def pull(self, now: datetime) -> dict:
        with self.lock:
            return self._pull(now)

    def _pull(self, now: datetime) -> dict:
        d, root = self.store.d, self.store.root
        today = now.date()
        stamp = now.isoformat(timespec="seconds")
        feeds = d.all_feeds(root)
        outlets = d.load_sources(root)
        state = {url: s for url, s in self.state().items() if url in {u for u, _ in feeds}}
        known = self._known(today)
        fresh, failed, last_host = [], 0, None
        for url, domain in feeds:
            host = urlsplit(url).netloc
            if host == last_host:
                self.sleep(SAME_HOST_PAUSE)
            last_host = host
            fs = state.setdefault(url, {})
            fs["checked"] = stamp
            result = self.fetcher(url, fs.get("etag"), fs.get("modified"))
            items = None
            if not result.error and result.status != 304:
                try:
                    items = parse_feed(result.body or b"")
                except ET.ParseError as exc:
                    result.error = f"not a feed: {exc}"[:120]
            if result.error:
                failed += 1
                fs["error"] = result.error
                fs["errors"] = fs.get("errors", 0) + 1
                continue
            fs.pop("error", None)
            fs["errors"] = 0
            fs["ok"] = stamp
            if items is None:
                continue
            ids = [i["id"] for i in items]
            previous = set(fs.get("ids", []))
            if previous and ids and not previous & set(ids):
                fs["gap"] = stamp
                log.warning("%s: nothing left from the previous fetch, items may be missed", url)
            fs.update(etag=result.etag, modified=result.modified, ids=ids[:100])
            lang = (outlets.get(domain, {}).get("languages") or [None])[0]
            added = 0
            for item in items:
                key = (domain, item["id"])
                if key in known:
                    continue
                known[key] = today.isoformat()
                fresh.append({**item, "domain": domain, "feed": url, "lang": lang, "seen": stamp})
                added += 1
            counts = {
                k: v for k, v in fs.get("new", {}).items() if k > str(today - timedelta(days=7))
            }
            counts[today.isoformat()] = counts.get(today.isoformat(), 0) + added
            fs["new"] = counts
        if fresh:
            self.items_dir.mkdir(parents=True, exist_ok=True)
            with (self.items_dir / f"{today.isoformat()}.jsonl").open("a", encoding="utf-8") as f:
                for item in fresh:
                    f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        self._prune(today)
        self._save_state(state)
        matched = self._rematch(today)
        log.info("wire: %d feeds, %d new items, %d failed", len(feeds), len(fresh), failed)
        return {"feeds": len(feeds), "new": len(fresh), "failed": failed, "matched": matched}

    def rematch(self, today: date) -> dict[str, int]:
        with self.lock:
            return self._rematch(today)

    def _rematch(self, today: date) -> dict[str, int]:
        d, root = self.store.d, self.store.root
        topics = [t for t in d.load_topics(root) if t["watch"] and t.get("active", True)]
        starts = {t["id"]: d.wire_start(root, t["id"], today) for t in topics}
        items = self.load_items(min(starts.values())) if starts else []
        counts = {}
        for t in topics:
            match = d.watch_matcher(t["watch"])
            hits = []
            for item in items:
                day = d.item_day(item)
                if day is None or day < starts[t["id"]]:
                    continue
                if found := match(f"{item.get('title', '')} {item.get('summary', '')}"):
                    hits.append({**item, "matched": found})
            d.write_wire(self.base, t["id"], hits)
            counts[t["id"]] = len(hits)
        for path in self.dir.glob("*.jsonl"):
            if path.stem not in counts:
                path.unlink()
        return counts

    def for_topic(self, topic_id: str, since: date | None, limit: int = TOPIC_PAGE_LIMIT):
        d = self.store.d
        items = [
            i
            for i in d.read_wire(self.base, topic_id)
            if (since is None or d.item_day(i) >= since)
            and str(i.get("url", "")).startswith(("https://", "http://"))
        ]
        return items[:limit], len(items)

    def publish(self, repo) -> int:
        """Copies the matches into the data repo's wire/ folder and commits once."""
        with self.lock:
            root = self.store.root
            folder = root / "wire"
            folder.mkdir(exist_ok=True)
            (folder / ".gitkeep").touch()
            ours = {p.name: p for p in self.dir.glob("*.jsonl")}
            for path in folder.glob("*.jsonl"):
                if path.name not in ours:
                    path.unlink()
            total = 0
            for name, path in ours.items():
                text = path.read_text(encoding="utf-8")
                total += text.count("\n")
                (folder / name).write_text(text, encoding="utf-8")
        repo.commit_and_push(["wire"], f"wire: {total} items for {len(ours)} topics")
        return total


def main(argv: list[str] | None = None) -> int:
    from telex.config import get_settings
    from telex.store import Store

    parser = argparse.ArgumentParser(prog="python -m telex.wire")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="fetch every listed feed once and report on it")
    p_find = sub.add_parser("discover", help="list the feeds a site advertises")
    p_find.add_argument("domain")
    sub.add_parser("pull", help="fetch now and update the topic matches (no commit)")
    args = parser.parse_args(argv)
    store = Store(get_settings())
    if args.cmd == "discover":
        for feed in discover(args.domain):
            print(f"{feed['url']}\t{feed['title']}")
        return 0
    if args.cmd == "pull":
        wire = Wire(store.settings.data_dir, store)
        print(json.dumps(wire.pull(store.now()), indent=2))
        return 0
    bad = 0
    for url, domain in store.d.all_feeds(store.root):
        row = check(url)
        if row["error"]:
            bad += 1
            print(f"FAIL  {domain:28} {url}\n      {row['error']}")
        else:
            newest = (row["newest"] or "no dates")[:16]
            print(
                f"ok    {domain:28} {row['items']:3} items, {row['with_summary']:3} with summary,"
                f" newest {newest}  {url}"
            )
    return 1 if bad else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
