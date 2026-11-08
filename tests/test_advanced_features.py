import asyncio
from pathlib import Path

import httpx
import pytest

import audio
import evolve
import llm
from modules import _web


def test_evolution_installs_only_validated_module_changes(monkeypatch, tmp_path):
    root = tmp_path / "installed"
    root.mkdir()
    evolve.git(root, "init", "-b", "main")
    evolve.git(root, "config", "user.name", "Test")
    evolve.git(root, "config", "user.email", "test@example.org")
    (root / "modules").mkdir()
    (root / "modules" / "example.py").write_text("VALUE=1")
    (root / "validate.py").write_text("pass")
    (root / "tests").mkdir()
    (root / "tests" / "test_example.py").write_text("def test_example(): assert True")
    evolve.git(root, "add", ".")
    evolve.git(root, "commit", "-qm", "initial")
    monkeypatch.setattr(evolve, "ROOT", root)
    monkeypatch.setenv("EVOLVE_SAFEWORD", "test-only")
    monkeypatch.setenv("EVOLVE_PUSH", "false")
    monkeypatch.setattr(llm, "LOCAL", False)
    monkeypatch.setattr(evolve, "_apply_request", lambda folder, request:
                        (folder / "modules" / "example.py").write_text("VALUE=2"))
    with pytest.raises(ValueError, match="safeword"):
        evolve.evolve("update", "bad")
    assert "Installed" in evolve.evolve("update", "test-only")
    assert (root / "modules" / "example.py").read_text() == "VALUE=2"
    monkeypatch.setattr(evolve, "_apply_request", lambda folder, request:
                        (folder / "audio.py").write_text("VOICE = 'stock'"))
    assert "audio.py" in evolve.evolve("extend speech", "test-only")
    monkeypatch.setattr(evolve, "_apply_request", lambda folder, request:
                        (folder / "validate.py").write_text("raise Exception"))
    with pytest.raises(ValueError, match="protected"):
        evolve.evolve("another update", "test-only")
    assert (root / "validate.py").read_text() == "pass"
    (root / "uncommitted.txt").write_text("keep")
    with pytest.raises(ValueError, match="existing work"):
        evolve.evolve("another update", "test-only")


def test_local_web_and_recovery_markers(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_LOCAL_WEB", "true")
    defs = _web.localize_tools([{"type": "web_search_20260209", "name": "web_search"},
                                {"type": "code_execution_20260120", "name": "code_execution"}])
    assert [t["name"] for t in defs] == ["web_search"] and "type" not in defs[0]
    for target in ("http://127.0.0.1/admin", "http://[::1]/", "http://localhost/", "file:///secrets"):
        assert "Could not" in asyncio.run(_web.fetch(target))
    marker = tmp_path / ".mygg-inference.json"
    monkeypatch.setattr(llm, "HEALTH_FILE", marker)
    llm.record_inference(True)
    llm.record_inference(True)
    import json
    assert json.loads(marker.read_text())["consecutive_timeouts"] == 2
    llm.record_inference(False)
    assert not marker.exists()


def test_voice_uses_the_selected_local_engine_without_a_personal_sample(monkeypatch):
    monkeypatch.setattr(audio, "TTS_ENGINE", "chatterbox")
    calls = []
    def synth(text, language, path):
        calls.append((text, language))
        path.write_bytes(b"test audio")
    monkeypatch.setattr(audio.synthesizer, "_local_speech", synth)
    path = Path(asyncio.run(audio.synthesizer.speak_async("Hi", "en")))
    try:
        assert calls == [("Hi", "en")] and path.read_bytes() == b"test audio"
    finally:
        path.unlink()
