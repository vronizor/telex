import os
import shutil
import subprocess
from pathlib import Path

import pytest

DATA_TEMPLATE = Path(__file__).parent.parent / "data-template"

os.environ.setdefault("BRIEFINGS_DATA_DIR", "/nonexistent")

REPORT_UPDATE = """+++
status = "update"
headline = "New reference index published; landlord groups appeal"
since = 2026-10-14
+++

## What changed

- [+] Index published in the gazette. ([Gencat](https://example.org/a))
- [+] Two associations appeal. ([Ara](https://example.org/b))
- [!] Whether the appeal suspends the cap is disputed. ([X](https://example.org/c))
- [-] The pending parliament vote, held last week.

## Where it stands

Cap in force.
"""

REPORT_QUIET = """+++
status = "quiet"
headline = "Nothing material since last week"
since = 2026-10-09
+++

Nothing material. Checked the Council agenda and Parliament committee pages.
"""


def git(*args, cwd=None):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def remote(tmp_path):
    src = tmp_path / "seed"
    shutil.copytree(DATA_TEMPLATE, src, ignore=shutil.ignore_patterns("__pycache__", ".git"))
    (src / "topics").mkdir(exist_ok=True)
    (src / "topics" / "bcn-rent-cap.toml").write_text(
        'title = "Barcelona rent cap"\ncadence = "weekly"\nslot = 2\n'
        'languages = ["ca", "es"]\nactive = true\ncreated = 2026-09-30\n'
        'brief = "Data over op-eds."\n'
    )
    (src / "topics" / "eu-chat-control.toml").write_text(
        'title = "EU chat control"\ncadence = "weekly"\nslot = 4\n'
        'languages = ["en", "fr"]\nactive = true\ncreated = 2026-09-30\nbrief = "Votes only."\n'
    )
    (src / "topics" / "paris-velo.toml").write_text(
        'title = "Paris cycling network"\ncadence = "monthly"\n'
        'languages = ["fr"]\nactive = true\ncreated = 2026-10-21\nbrief = "Plan Vélo."\n'
    )
    for topic, day, text in (
        ("bcn-rent-cap", "2026-10-14", REPORT_UPDATE.replace("2026-10-14", "2026-10-07")),
        ("bcn-rent-cap", "2026-10-21", REPORT_UPDATE),
        ("eu-chat-control", "2026-10-16", REPORT_QUIET),
    ):
        folder = src / "reports" / topic
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day}.md").write_text(text)
    (src / "queue").mkdir(exist_ok=True)
    (src / "queue" / ".gitkeep").touch()
    git("init", "-q", "-b", "main", cwd=src)
    git("add", "-A", cwd=src)
    git("commit", "-q", "-m", "seed", cwd=src)
    bare = tmp_path / "remote.git"
    git("clone", "-q", "--bare", str(src), str(bare))
    return bare


@pytest.fixture
def client(tmp_path, remote, monkeypatch):
    from fastapi.testclient import TestClient

    from briefings import app as app_module
    from briefings.config import Settings
    from briefings.gitrepo import Repo
    from briefings.store import Store

    base = app_module.settings
    settings = Settings(
        repo_url=str(remote),
        data_dir=tmp_path / "data",
        timezone=base.timezone,
        dispatch_time=base.dispatch_time,
        sync_minutes=60,
        git_name="test",
        git_email="test@example.org",
    )
    monkeypatch.setattr(app_module, "settings", settings)
    monkeypatch.setattr(
        app_module, "repo", Repo(settings.checkout, str(remote), "test", "test@example.org")
    )
    monkeypatch.setattr(app_module, "store", Store(settings))
    with TestClient(app_module.app, base_url="http://briefings.test") as c:
        yield c


def remote_file(remote: Path, path: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"HEAD:{path}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None
