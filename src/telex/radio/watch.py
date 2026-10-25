"""Watch the data checkout for show scripts and render each new or changed one in a child process.

Keeps the model out of memory between renders. Stdlib only.
"""

import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telex.radio import library

log = logging.getLogger("telex.radio")


def pending(
    checkout: Path, data_dir: Path, today: date, keep_days: int, settle_s: float = 20
) -> list[tuple[date, str]]:
    first = today - timedelta(days=keep_days - 1)
    found = []
    for path in sorted(library.shows_dir(checkout).glob("*.md"), reverse=True):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not first <= day <= today or time.time() - path.stat().st_mtime < settle_s:
            continue
        digest = library.digest(path)
        meta = library.read_meta(data_dir, day) or {}
        if digest in (meta.get("digest"), (meta.get("failed") or {}).get("digest")):
            continue
        found.append((day, digest))
    return found


def prune(data_dir: Path, today: date, keep_days: int) -> list[Path]:
    first = today - timedelta(days=keep_days - 1)
    removed = []
    for path in library.audio_dir(data_dir).glob("*"):
        try:
            day = date.fromisoformat(path.name.split(".")[0])
        except ValueError:
            continue
        if day < first:
            path.unlink(missing_ok=True)
            removed.append(path)
    return removed


def render(checkout: Path, data_dir: Path, day: date, nice: int, timeout_s: int) -> str | None:
    command = [
        sys.executable,
        "-m",
        "telex.radio.render",
        "--checkout",
        str(checkout),
        "--data-dir",
        str(data_dir),
        "--date",
        day.isoformat(),
    ]
    if nice:
        command = ["nice", "-n", str(nice), *command]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return f"render took longer than {timeout_s // 60} minutes"
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        return result.stderr.strip() or f"render exited with {result.returncode}"
    return None


def tick(checkout: Path, data_dir: Path, today: date, keep_days: int, nice: int, timeout_s: int):
    for day, digest in pending(checkout, data_dir, today, keep_days):
        log.info("rendering %s", day)
        error = render(checkout, data_dir, day, nice, timeout_s)
        if error:
            log.error("render of %s failed: %s", day, error.splitlines()[-1])
            library.record_failure(data_dir, day, digest, error)
    for path in prune(data_dir, today, keep_days):
        log.info("removed %s", path.name)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    env = os.environ.get
    data_dir = Path(env("TELEX_DATA_DIR", "/data"))
    checkout = data_dir / "repo"
    tz = ZoneInfo(env("TELEX_TZ", "Europe/Madrid"))
    keep_days = int(env("TELEX_RADIO_KEEP_DAYS", "14"))
    poll_s = int(env("TELEX_RADIO_POLL_SECONDS", "60"))
    nice = int(env("TELEX_RADIO_NICE", "10"))
    timeout_s = int(env("TELEX_RADIO_TIMEOUT_MINUTES", "60")) * 60
    log.info(
        "watching %s every %ss, keeping %s days", library.shows_dir(checkout), poll_s, keep_days
    )
    while True:
        try:
            tick(checkout, data_dir, datetime.now(tz).date(), keep_days, nice, timeout_s)
        except Exception:
            log.exception("watch loop failed")
        time.sleep(poll_s)


if __name__ == "__main__":
    sys.exit(main())
