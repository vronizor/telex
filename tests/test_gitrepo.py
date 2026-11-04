import subprocess

from telex.gitrepo import Repo


def test_pull_timeout_is_reported_not_raised(tmp_path, monkeypatch):
    repo = Repo(tmp_path, "", "t", "t@example.com")

    def slow(*args):
        raise subprocess.TimeoutExpired(["git", "pull"], 120)

    monkeypatch.setattr(repo, "_git", slow)
    assert repo.pull() is False
    assert repo.last_error.startswith("git pull:")
