import subprocess
import threading
from datetime import datetime
from pathlib import Path


class GitError(RuntimeError):
    pass


class Repo:
    def __init__(self, path: Path, url: str, name: str, email: str):
        self.path = path
        self.url = url
        self.identity = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
        self.lock = threading.Lock()
        self.last_sync: datetime | None = None
        self.last_error: str | None = None

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *self.identity, "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )

    def _check(self, *args: str) -> str:
        result = self._git(*args)
        if result.returncode != 0:
            raise GitError(f"git {args[0]}: {(result.stderr or result.stdout).strip()}")
        return result.stdout

    def last_commit(self, path: str) -> str | None:
        result = self._git("log", "-1", "--format=%H", "--", path)
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and sha else None

    def ensure(self) -> None:
        if (self.path / ".git").exists():
            return
        if not self.url:
            raise GitError(f"no checkout at {self.path} and TELEX_REPO_URL is not set")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "clone", self.url, str(self.path)], capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise GitError(f"git clone: {result.stderr.strip()}")

    def pull(self) -> bool:
        with self.lock:
            try:
                self._check("pull", "--rebase", "--autostash", "--quiet")
            except GitError as exc:
                self.last_error = str(exc)
                return False
            self.last_sync = datetime.now().astimezone()
            self.last_error = None
            return True

    def commit_and_push(self, paths: list[str], message: str) -> None:
        with self.lock:
            self._check("pull", "--rebase", "--autostash", "--quiet")
            self._check("add", "-A", "--", *paths)
            if self._git("diff", "--cached", "--quiet").returncode == 0:
                return
            self._check("commit", "--quiet", "-m", message)
            if self._git("push", "--quiet").returncode != 0:
                self._check("pull", "--rebase", "--quiet")
                self._check("push", "--quiet")
            self.last_sync = datetime.now().astimezone()
            self.last_error = None
