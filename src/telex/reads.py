import json
import os
import threading
from datetime import date, datetime
from pathlib import Path

SHOW_STATES = ("started", "done")


class Reads:
    """What the reader has seen, shared by every device: per topic the newest report day read and
    when its wire was last looked at, per radio show whether it was started or finished.
    Everything up to `baseline` (the first time the file was created) counts as read."""

    def __init__(self, data_dir: Path, clock):
        self.path = data_dir / "read.json"
        self.clock = clock
        self.lock = threading.Lock()
        self._state: dict | None = None

    @property
    def state(self) -> dict:
        with self.lock:
            if self._state is None:
                self._state = self._load()
            return self._state

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
        if not isinstance(state, dict) or "baseline" not in state:
            state = {"baseline": self.clock().isoformat(timespec="seconds")}
            self._write(state)
        for key in ("reports", "wire", "shows"):
            if not isinstance(state.get(key), dict):
                state[key] = {}
        return state

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _set(self, section: str, key: str, value: str) -> None:
        state = self.state
        with self.lock:
            state[section][key] = value
            self._write(state)

    @property
    def baseline(self) -> datetime:
        return datetime.fromisoformat(self.state["baseline"])

    def report_read(self, topic_id: str) -> date:
        stored = self.state["reports"].get(topic_id)
        floor = self.baseline.date()
        return max(date.fromisoformat(stored), floor) if stored else floor

    def mark_report(self, topic_id: str, day: date) -> None:
        if day <= self.report_read(topic_id):
            return
        self._set("reports", topic_id, day.isoformat())

    def wire_seen(self, topic_id: str) -> datetime:
        stored = self.state["wire"].get(topic_id)
        return max(datetime.fromisoformat(stored), self.baseline) if stored else self.baseline

    def mark_wire(self, topic_id: str, when: datetime) -> None:
        self._set("wire", topic_id, when.isoformat(timespec="seconds"))

    def show(self, day: date) -> str | None:
        if day <= self.baseline.date():
            return "done"
        return self.state["shows"].get(day.isoformat())

    def mark_show(self, day: date, state: str) -> None:
        if state not in SHOW_STATES or self.show(day) in (state, "done"):
            return
        self._set("shows", day.isoformat(), state)

    def forget_shows_before(self, day: date) -> None:
        state = self.state
        with self.lock:
            old = [k for k in state["shows"] if k < day.isoformat()]
            for k in old:
                del state["shows"][k]
            if old:
                self._write(state)


def is_new(item: dict, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(item.get("seen", ""))) > since
    except (ValueError, TypeError):
        return False
