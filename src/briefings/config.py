import os
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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

    @property
    def checkout(self) -> Path:
        return self.data_dir / "repo"


@lru_cache
def get_settings() -> Settings:
    env = os.environ.get
    return Settings(
        repo_url=env("BRIEFINGS_REPO_URL", ""),
        data_dir=Path(env("BRIEFINGS_DATA_DIR", "/data")),
        timezone=ZoneInfo(env("BRIEFINGS_TZ", "Europe/Madrid")),
        dispatch_time=time.fromisoformat(env("BRIEFINGS_DISPATCH_TIME", "06:45")),
        sync_minutes=int(env("BRIEFINGS_SYNC_MINUTES", "30")),
        git_name=env("BRIEFINGS_GIT_NAME", "briefings-pi"),
        git_email=env("BRIEFINGS_GIT_EMAIL", "briefings-pi@localhost"),
        theme=env("BRIEFINGS_THEME", "wire"),
        base_url=env("BRIEFINGS_BASE_URL", "").rstrip("/"),
        edition_weekday=WEEKDAYS.index(env("BRIEFINGS_EDITION_DAY", "sat").lower()[:3]),
        edition_time=time.fromisoformat(env("BRIEFINGS_EDITION_TIME", "08:00")),
    )
