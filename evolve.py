"""Owner-requested module changes in a disposable Git clone, validated before installation."""
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parent
_lock = threading.Lock()
PROTECTED = {
    "main.py", "agent.py", "evolve.py", "validate.py", "tools.py", "constants.py",
    "utils.py", "whatsapp_server.py", "whoop_auth.py", "setup_calendar.py", "supervise.py",
    "llm.py", "_outgoing.py", "_web.py", "_documents.py", "__init__.py",
}


def authorized(word: str) -> bool:
    secret = os.getenv("EVOLVE_SAFEWORD", "")
    return bool(secret) and hmac.compare_digest(word.encode(), secret.encode())


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=folder, capture_output=True, check=True,
                          encoding="utf-8", timeout=60).stdout.strip()


def _apply_request(folder: Path, request: str) -> None:
    cli = shutil.which("claude")
    if not cli:
        raise ValueError("Install Claude Code and complete its setup before using /evolve.")
    # Give the editor only source files, never the running installation's bot/integration keys.
    allowed_env = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "HOME",
                   "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
                   "TMP", "TEMP", "TMPDIR", "LANG", "ANTHROPIC_API_KEY"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed_env}
    subprocess.run([cli, "--bare", "--print", "--model", "sonnet",
                    "--tools", "Read,Glob,Grep,Edit,Write", "--permission-mode", "acceptEdits",
                    "--allowedTools", "Read,Glob,Grep,Edit,Write", "--max-turns", "15",
                    "--append-system-prompt", "Implement the requested assistant feature in application Python, modules, "
                    "tests or Markdown documentation. Follow modules/_template.py when adding tools. "
                    "Do not change Git state, credentials, release configuration or protected security checks.",
                    "--", request], cwd=folder, env=env, capture_output=True, check=True, timeout=600)


def _validate(folder: Path, changes: list[str]) -> None:
    for name in changes:
        path = Path(name)
        if (path.suffix.lower() not in {".py", ".md"} or path.name in PROTECTED
                or any(part.startswith(".") or part == "private" for part in path.parts)
                or path.name == "personality.md" or (folder / path).is_symlink()
                or not (folder / path).resolve().is_relative_to(folder.resolve())):
            raise ValueError("The proposed change touched a protected path.")
    # Exercise the new module with isolated settings. The real .env and runtime data stay in ROOT.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "PATHEXT", "SYSTEMROOT", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA",
        "TMP", "TEMP", "TMPDIR", "LANG", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}}
    env.update(LLM_BACKEND="local", ENABLE_MEMORY="false", TELEGRAM_BOT_TOKEN="1234:offline",
               ALLOWED_USER_IDS="42", PYTHONUTF8="1")
    subprocess.run([sys.executable, "-m", "compileall", "-q", "."], cwd=folder, env=env,
                   capture_output=True, check=True, timeout=120)
    subprocess.run([sys.executable, "validate.py"], cwd=folder, env=env,
                   capture_output=True, check=True, timeout=120)
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=folder, env=env,
                   capture_output=True, check=True, timeout=300)


def evolve(request: str, word: str) -> str:
    import llm
    if not authorized(word) or llm.LOCAL:
        raise ValueError("Code changes need the configured safeword and Claude mode.")
    if not request.strip() or len(request) > 4000:
        raise ValueError("Describe the requested change in up to 4000 characters.")
    if not _lock.acquire(blocking=False):
        raise ValueError("Another update is in progress.")
    try:
        branch = git(ROOT, "symbolic-ref", "--short", "HEAD")
        start = git(ROOT, "rev-parse", "HEAD")
        if git(ROOT, "status", "--porcelain"):
            raise ValueError("Commit or set aside your existing work before updating.")
        with tempfile.TemporaryDirectory(prefix="mygg-evolve-") as tmp:
            folder = Path(tmp)
            git(ROOT, "clone", "--quiet", "--no-local", "--single-branch", "--branch", branch, str(ROOT), str(folder))
            _apply_request(folder, request)
            if git(folder, "rev-parse", "HEAD") != start:
                raise ValueError("The editor changed Git history. The update was discarded.")
            git(folder, "add", "-A")
            changes = git(folder, "diff", "--cached", "--no-renames", "--name-only", "-z").strip("\0").split("\0")
            if changes == [""]:
                raise ValueError("The editor proposed no source changes.")
            _validate(folder, changes)
            git(folder, "-c", "user.name=MyGG", "-c", "user.email=mygg@localhost",
                "commit", "-qm", "Apply requested MyGG feature update")
            if (git(ROOT, "rev-parse", "HEAD") != start or git(ROOT, "status", "--porcelain")
                    or git(ROOT, "symbolic-ref", "--short", "HEAD") != branch):
                raise ValueError("Your checkout changed during validation. The update was discarded.")
            git(ROOT, "fetch", "--no-tags", str(folder), "HEAD")
            git(ROOT, "merge", "--ff-only", "FETCH_HEAD")
        message = "Installed and tested: " + ", ".join(changes)
        if os.getenv("EVOLVE_PUSH", "").lower() == "true":
            try:
                git(ROOT, "push", "origin", branch)
            except subprocess.SubprocessError:
                message += ". Saved locally. The push to your fork failed."
        return message
    finally:
        _lock.release()
