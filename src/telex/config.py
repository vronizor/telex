import os
import re
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
GITHUB_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"([\w.-]+/[\w.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class Settings:
    repo_url: str
    data_dir: Path
    timezone: ZoneInfo
    dispatch_time: time
    sync_minutes: int
    git_name: str
    git_email: str
    theme: str = "wire"
    base_url: str = ""
    edition_weekday: int = 5
    edition_time: time = time(8, 0)
    radio_keep_days: int = 14

    @property
    def checkout(self) -> Path:
        return self.data_dir / "repo"

    @property
    def github_repo(self) -> str | None:
        m = GITHUB_RE.match(self.repo_url.strip())
        return m.group(1) if m else None


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        repo_url=env("TELEX_REPO_URL", ""),
        data_dir=Path(env("TELEX_DATA_DIR", "/data")),
        timezone=ZoneInfo(env("TELEX_TZ", "Europe/Madrid")),
        dispatch_time=time.fromisoformat(env("TELEX_DISPATCH_TIME", "06:45")),
        sync_minutes=int(env("TELEX_SYNC_MINUTES", "30")),
        git_name=env("TELEX_GIT_NAME", "telex-pi"),
        git_email=env("TELEX_GIT_EMAIL", "telex-pi@localhost"),
        theme=env("TELEX_THEME", "wire"),
        base_url=env("TELEX_BASE_URL", "").rstrip("/"),
        edition_weekday=WEEKDAYS.index(env("TELEX_EDITION_DAY", "sat").lower()[:3]),
        edition_time=time.fromisoformat(env("TELEX_EDITION_TIME", "08:00")),
        radio_keep_days=int(env("TELEX_RADIO_KEEP_DAYS", "14")),
    )
