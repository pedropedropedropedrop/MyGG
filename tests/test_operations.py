from datetime import date
import json
import subprocess
import sys

import pytest
import connection_health
import supervise


def test_health_transitions_and_weekly_digest():
    monday, tuesday = date(2026, 11, 9), date(2026, 11, 10)
    lines, state = connection_health.report({"Google": True, "WhatsApp": False}, {}, monday)
    assert "WhatsApp needs attention." in lines and len(lines) == 2
    assert connection_health.report(state["services"], state, monday)[0] == []
    assert connection_health.report({"WhatsApp": True}, state, tuesday)[0] == ["WhatsApp has recovered."]


def test_only_dedicated_stalled_instance_can_be_restarted(tmp_path, monkeypatch):
    for url in ("http://127.0.0.1:11434", "http://example.com:11435", "http://127.0.0.1:11435/path"):
        with pytest.raises(ValueError):
            supervise.dedicated_endpoint(url)
    url = "http://127.0.0.1:11435"
    assert supervise.dedicated_endpoint(url) == ("127.0.0.1", 11435)
    marker = tmp_path / "status.json"
    monkeypatch.setattr(supervise, "FAILURE", marker)
    marker.write_text(json.dumps({"failed_at": 100, "consecutive_timeouts": 2, "base_url": url}))
    assert supervise.inference_stalled(url, now=110)
    assert not supervise.inference_stalled(url, now=1100)
    assert not supervise.inference_stalled("http://127.0.0.1:9999", now=110)


def test_single_instance_lock_releases_after_exit(tmp_path):
    body = ("from pathlib import Path; import supervise; "
            f"c=supervise.instance_lock('bot',Path({str(tmp_path)!r})); c.__enter__()")
    with supervise.instance_lock("bot", tmp_path):
        assert subprocess.run([sys.executable, "-c", body], cwd=supervise.ROOT).returncode != 0
    assert subprocess.run([sys.executable, "-c", body], cwd=supervise.ROOT).returncode == 0


def test_updates_validate_candidate_preserve_edits_and_rollback_bad_live_config(tmp_path):
    origin, clone = tmp_path / "origin", tmp_path / "installed"
    origin.mkdir()
    def git(repo, *args):
        return supervise.git(*args, cwd=repo)
    git(origin, "init", "-b", "main")
    git(origin, "config", "user.name", "Test")
    git(origin, "config", "user.email", "test@example.invalid")
    git(origin, "config", "commit.gpgsign", "false")
    (origin / ".gitignore").write_text("runtime.txt\n__pycache__/\n")
    (origin / "validate.py").write_text("pass\n")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "initial")
    git(tmp_path, "clone", str(origin), str(clone))
    (origin / "feature.txt").write_text("feature")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "feature")
    (clone / "local.txt").write_text("private untracked work")
    stopped = []
    supervise.checked_update(lambda: stopped.append(True), root=clone)
    assert not stopped and not (clone / "feature.txt").exists()
    (clone / "local.txt").unlink()
    supervise.checked_update(lambda: stopped.append(True), root=clone)
    assert stopped and (clone / "feature.txt").read_text() == "feature"
    good = git(clone, "rev-parse", "HEAD")
    (clone / "runtime.txt").write_text("private runtime")
    (origin / "validate.py").write_text("from pathlib import Path\nassert not Path('runtime.txt').exists()\n")
    git(origin, "commit", "-am", "bad live install")
    rejected = set()
    with pytest.raises(RuntimeError):
        supervise.checked_update(lambda: None, root=clone, rejected=rejected)
    assert git(clone, "rev-parse", "HEAD") == good
    assert (clone / "runtime.txt").read_text() == "private runtime"
    supervise.checked_update(lambda: pytest.fail("retried a rejected revision"), root=clone, rejected=rejected)
