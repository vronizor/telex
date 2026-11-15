"""Where shows and their audio live, and what state each one is in. Stdlib only: the app uses it."""

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

SPECIALS = "specials"
SPECIAL_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]{0,59})$")
Key = date | str


def shows_dir(checkout: Path) -> Path:
    return checkout / "shows"


def specials_dir(checkout: Path) -> Path:
    return checkout / SPECIALS


def is_special(key: Key) -> bool:
    return str(key).startswith(f"{SPECIALS}/")


def script_path(checkout: Path, key: Key) -> Path:
    """A morning show is keyed by its date, a special by 'specials/<date>-<topic>'."""
    return checkout / f"{key}.md" if is_special(key) else shows_dir(checkout) / f"{key}.md"


def parse_special_key(key: Key) -> tuple[date, str] | None:
    m = SPECIAL_RE.match(str(key).removeprefix(f"{SPECIALS}/"))
    if not m or not is_special(key):
        return None
    try:
        return date.fromisoformat(m.group(1)), m.group(2)
    except ValueError:
        return None


def audio_dir(data_dir: Path) -> Path:
    return data_dir / "audio"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def read_meta(data_dir: Path, day: Key) -> dict | None:
    path = audio_dir(data_dir) / f"{day}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_meta(data_dir: Path, day: Key, meta: dict) -> None:
    payload = json.dumps(meta, indent=2, ensure_ascii=False).encode()
    write_atomic(audio_dir(data_dir) / f"{day}.json", payload)


def record_failure(data_dir: Path, day: Key, digest: str, error: str) -> None:
    meta = read_meta(data_dir, day) or {}
    meta["failed"] = {
        "digest": digest,
        "error": error[-2000:],
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    write_meta(data_dir, day, meta)


@dataclass
class Chapter:
    start: float
    kind: str
    topics: list[dict]
    label: str = ""

    @property
    def title(self) -> str:
        if self.label:
            return self.label
        if self.kind == "intro":
            return "Intro"
        if self.kind == "outro":
            return "Outro"
        if self.kind == "cast":
            return "Who's who"
        names = [t.get("title") or t["id"] for t in self.topics]
        return ("Quiet: " if self.kind == "quiet" else "") + ", ".join(names)


@dataclass
class Show:
    day: date
    state: str
    duration: float = 0
    chapters: list[Chapter] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    error: str = ""
    stale: bool = False
    digest: str = ""
    key: str = ""
    topic: str = ""
    title: str = ""

    def __post_init__(self):
        self.key = self.key or self.day.isoformat()

    @property
    def special(self) -> bool:
        return is_special(self.key)

    @property
    def minutes(self) -> int:
        return max(1, round(self.duration / 60))

    @property
    def topic_chapters(self) -> list[Chapter]:
        return [c for c in self.chapters if c.kind in ("topic", "quiet", "story")]


def special_title(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
        front = text.split("+++", 2)[1] if text.startswith("+++") else ""
        return str(tomllib.loads(front).get("title", "")).strip()
    except (OSError, IndexError, tomllib.TOMLDecodeError):
        return ""


def show_state(checkout: Path, data_dir: Path, day: Key) -> Show | None:
    special = parse_special_key(day) if is_special(day) else None
    if is_special(day) and special is None:
        return None
    script = script_path(checkout, day)
    meta = read_meta(data_dir, day) or {}
    has_audio = (audio_dir(data_dir) / f"{day}.mp3").exists() and "duration" in meta
    if not script.exists() and not has_audio:
        return None
    script_digest = digest(script) if script.exists() else None
    failed = meta.get("failed") or {}
    failed_now = script_digest is not None and failed.get("digest") == script_digest
    if has_audio:
        state = "ready"
    elif failed_now:
        state = "failed"
    else:
        state = "recording"
    return Show(
        day=special[0] if special else day,
        key=str(day),
        topic=special[1] if special else "",
        title=(special_title(script) or meta.get("title", "")) if special else "",
        state=state,
        duration=meta.get("duration", 0) if has_audio else 0,
        chapters=[Chapter(**c) for c in meta.get("chapters", [])] if has_audio else [],
        problems=meta.get("problems", []) if has_audio else [],
        error=failed.get("error", "") if failed_now else "",
        stale=has_audio and script_digest is not None and meta.get("digest") != script_digest,
        digest=meta.get("digest", "") if has_audio else "",
    )


def show_days(checkout: Path, data_dir: Path) -> list[date]:
    days = set()
    for folder, suffix in ((shows_dir(checkout), ".md"), (audio_dir(data_dir), ".mp3")):
        for path in folder.glob(f"*{suffix}"):
            try:
                days.add(date.fromisoformat(path.stem))
            except ValueError:
                continue
    return sorted(days, reverse=True)


def recent_shows(checkout: Path, data_dir: Path, today: date, keep_days: int) -> list[Show]:
    first = today - timedelta(days=keep_days - 1)
    shows = []
    for day in show_days(checkout, data_dir):
        if first <= day <= today:
            show = show_state(checkout, data_dir, day)
            if show:
                shows.append(show)
    return shows


def special_keys(checkout: Path, data_dir: Path) -> list[str]:
    stems = {p.stem for p in specials_dir(checkout).glob("*.md")}
    stems |= {p.stem for p in (audio_dir(data_dir) / SPECIALS).glob("*.mp3")}
    keys = [f"{SPECIALS}/{s}" for s in stems if parse_special_key(f"{SPECIALS}/{s}")]
    return sorted(keys, key=lambda k: (parse_special_key(k)[0], k), reverse=True)


def specials(checkout: Path, data_dir: Path, topic: str | None = None) -> list[Show]:
    found = []
    for key in special_keys(checkout, data_dir):
        if topic is None or parse_special_key(key)[1] == topic:
            show = show_state(checkout, data_dir, key)
            if show:
                found.append(show)
    return found
