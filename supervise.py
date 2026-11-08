"""Opt-in process supervision and checked fast-forward updates for one MyGG folder."""

import argparse
from contextlib import contextmanager
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import zipfile

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("supervisor")
FAILURE = ROOT / ".mygg-inference.json"
STOP_REQUEST = ROOT / ".mygg-supervisor-stop"


@contextmanager
def instance_lock(name, directory=ROOT):
    """The OS releases this local lock even if the process crashes."""
    with (directory / f".mygg-{name}.lock").open("a+b") as handle:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            acquire()
        except OSError as exc:
            raise SystemExit(f"Another {name} already owns this MyGG folder.") from exc
        yield


def run(args, cwd=ROOT, timeout=90):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace",
                            timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode:
        # Git errors and package diagnostics can include private remote URLs or credentials.
        raise RuntimeError(f"{Path(str(args[0])).name} {args[1]} failed (exit {result.returncode})")
    return result.stdout.strip()


def git(*args, cwd=ROOT):
    return run(["git", *args], cwd=cwd)


def update_candidate(root=ROOT):
    """Return the pinned upstream commit only for clean, non-divergent checkouts."""
    if git("status", "--porcelain", cwd=root):
        return None
    branch = git("symbolic-ref", "--short", "HEAD", cwd=root)
    remote = git("config", "--get", f"branch.{branch}.remote", cwd=root)
    git("fetch", "--quiet", remote, cwd=root)
    old = git("rev-parse", "HEAD", cwd=root)
    new = git("rev-parse", "@{upstream}", cwd=root)
    if new == old or git("merge-base", old, new, cwd=root) != old:
        return None
    return branch, old, new


def checked_update(stop_children, root=ROOT, rejected=None):
    candidate = update_candidate(root)
    if not candidate:
        return
    branch, old, new = candidate
    if rejected is not None and new in rejected:
        return
    with tempfile.TemporaryDirectory(prefix="mygg-update-") as scratch:
        # Use only committed source. Personal files, tokens and databases stay in place.
        archive = subprocess.run(["git", "archive", "--format=zip", new], cwd=root,
                                 capture_output=True, check=True, timeout=90).stdout
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            source.extractall(scratch)
        run([sys.executable, "-m", "compileall", "-q", "."], cwd=scratch)
        system_vars = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "HOME", "USERPROFILE",
                       "TMP", "TEMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "LANG", "WINDIR"}
        sample = {k: v for k, v in os.environ.items() if k.upper() in system_vars}
        sample.update(LLM_BACKEND="local", ENABLE_MEMORY="false", OWNER_CHAT_ID="",
                      ALLOWED_USER_IDS="1", TELEGRAM_BOT_TOKEN="123456:offline-test", PYTHONUTF8="1")
        check = subprocess.run([sys.executable, "validate.py"], cwd=scratch, env=sample,
                               capture_output=True, timeout=90)
        if check.returncode:
            raise RuntimeError("Candidate setup check failed. Review dependencies or update manually.")
    if (git("symbolic-ref", "--short", "HEAD", cwd=root) != branch or
            git("rev-parse", "HEAD", cwd=root) != old or git("status", "--porcelain", cwd=root)):
        return
    if git("diff", "--name-only", old, new, "--", "requirements.txt", "requirements/",
           "whatsapp-bridge/package.json", "whatsapp-bridge/package-lock.json", cwd=root):
        raise RuntimeError("Dependencies changed. Follow the update guide before restarting.")
    stop_children()
    git("merge", "--ff-only", "--no-overwrite-ignore", new, cwd=root)
    try:
        run([sys.executable, "validate.py"], cwd=root)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        if rejected is not None:
            rejected.clear()
            rejected.add(new)
        if (git("symbolic-ref", "--short", "HEAD", cwd=root) == branch and
                git("rev-parse", "HEAD", cwd=root) == new and not git("status", "--porcelain", cwd=root)):
            git("reset", "--keep", old, cwd=root)
        raise
    LOG.info("Installed checked revision %s", new[:12])


def dedicated_endpoint(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.username or parsed.password or parsed.path not in {"", "/"} or
            parsed.query or parsed.fragment or not parsed.port or parsed.port == 11434):
        raise ValueError("Managed Ollama needs LOCAL_BASE_URL=http://127.0.0.1:<dedicated port>, such as 11435.")
    return parsed.hostname, parsed.port


def inference_stalled(url, now=None):
    try:
        data = json.loads(FAILURE.read_text(encoding="utf-8"))
        elapsed = (time.time() if now is None else now) - float(data["failed_at"])
        return (data["base_url"].rstrip("/") == url.rstrip("/") and 0 <= elapsed <= 900
                and int(data["consecutive_timeouts"]) >= 2)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def is_listening(endpoint):
    try:
        with socket.create_connection(endpoint, timeout=2):
            return True
    except OSError:
        return False


class Child:
    def __init__(self, name, argv, cwd=ROOT, env=None):
        self.name, self.argv, self.cwd, self.env = name, argv, cwd, env
        self.process = None
        self.next_start = 0.0

    def ensure(self):
        if self.process is not None and self.process.poll() is not None:
            LOG.warning("%s exited (%d)", self.name, self.process.returncode)
            self.process = None
            self.next_start = time.monotonic() + 10
        if self.process is None and time.monotonic() >= self.next_start:
            self.process = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, errors="replace",
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            stream = self.process.stdout
            def copy_logs():
                with stream:
                    for line in stream:
                        LOG.info("[%s] %s", self.name, line.rstrip())
            threading.Thread(target=copy_logs, daemon=True).start()
            LOG.info("Started %s", self.name)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.process = None


def supervise(options):
    load_dotenv(ROOT / ".env")
    STOP_REQUEST.unlink(missing_ok=True)
    # Detect an existing manual run before spawning a supervised copy.
    with instance_lock("bot"):
        pass
    children = [Child("bot", [sys.executable, "main.py"], env={**os.environ, "MYGG_SUPERVISED": "1"})]
    if options.bridge:
        node = shutil.which("node")
        if not node or not os.getenv("WHATSAPP_BRIDGE_SECRET", "").strip():
            raise SystemExit("Configure WhatsApp and install Node before enabling --bridge.")
        children.append(Child("WhatsApp", [node, "index.js"], ROOT / "whatsapp-bridge"))
    model = None
    if options.ollama:
        url = os.getenv("LOCAL_BASE_URL", "").rstrip("/")
        endpoint = dedicated_endpoint(url)
        binary = shutil.which("ollama")
        if not binary or is_listening(endpoint):
            raise SystemExit("Managed Ollama needs an installed ollama executable and an unused dedicated port.")
        model = Child("dedicated Ollama", [binary, "serve"], env={**os.environ, "OLLAMA_HOST": url})
    def stop_app():
        for child in reversed(children):
            child.stop()
    def stop_requested(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_requested)
    next_update = time.monotonic() + 300
    rejected = set()
    try:
        while True:
            if STOP_REQUEST.exists():
                STOP_REQUEST.unlink(missing_ok=True)
                return
            if model:
                if model.process and model.process.poll() is None and inference_stalled(url):
                    model.stop()
                    FAILURE.unlink(missing_ok=True)
                    LOG.warning("Restarting dedicated Ollama after repeated inference timeouts")
                if (model.process is None or model.process.poll() is not None) and is_listening(endpoint):
                    raise RuntimeError("Dedicated port now belongs to another process. Stop and check the configuration.")
                model.ensure()
            for child in children:
                child.ensure()
            if options.updates and time.monotonic() >= next_update:
                next_update = time.monotonic() + 300
                try:
                    checked_update(stop_app, rejected=rejected)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    LOG.warning("Automatic update skipped (%s)", exc)
            time.sleep(3)
    finally:
        stop_app()
        if model:
            model.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", action="store_true", help="supervise the configured WhatsApp bridge")
    parser.add_argument("--updates", action="store_true", help="check the Git upstream every five minutes")
    parser.add_argument("--ollama", action="store_true", help="own a dedicated loopback Ollama process")
    parser.add_argument("--stop", action="store_true", help="request a clean shutdown of this supervisor")
    options = parser.parse_args()
    if options.stop:
        STOP_REQUEST.touch()
        return
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[RotatingFileHandler(logs / "supervisor.log", maxBytes=2_000_000,
                                                      backupCount=2, encoding="utf-8")])
    with instance_lock("supervisor"):
        try:
            supervise(options)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
