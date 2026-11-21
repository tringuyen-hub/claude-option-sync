"""Cross-platform plumbing: settings.json hook install, mod config, the instance lock, and a real
background broker started, queried and stopped through the CLI over TCP (the Windows path)."""
import json
import os
import subprocess
import sys

import pytest

from claude_option.cli import HOOK_MODULE, main
from claude_option.utils.platform import IS_WINDOWS, LockHeld, lock_file


@pytest.fixture
def root(tmp_path, monkeypatch):
    """An isolated project root: own config, data dir and claude-mod folder."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("broker:\n  api_transport: tcp\n")
    (tmp_path / "claude-mod" / "hooks").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_OPTION_ROOT", str(tmp_path))
    return tmp_path


def _hooks(path):
    return json.loads(path.read_text())["hooks"]["PreToolUse"]


def test_install_hook_merges_and_is_idempotent(root):
    settings = root / "settings.json"
    settings.write_text(json.dumps({
        "model": "opus",
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/guard.sh"}]},
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "/old/scripts/run_hook.sh"}]},
        ]},
    }))
    for _ in range(2):
        assert main(["install-hook", "--settings", str(settings), "--form", "exec"]) == 0
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    pre = data["hooks"]["PreToolUse"]
    assert [e["matcher"] for e in pre] == ["Bash", "AskUserQuestion"]       # old copy replaced, not duplicated
    ours = pre[1]["hooks"][0]
    assert ours["command"] == sys.executable and ours["args"] == ["-m", HOOK_MODULE]


def test_install_hook_default_form_per_os(root):
    settings = root / "s.json"
    main(["install-hook", "--settings", str(settings)])
    hook = _hooks(settings)[0]["hooks"][0]
    if IS_WINDOWS:
        assert hook["args"] == ["-m", HOOK_MODULE]          # exec form: no shell, spaces in paths are fine
    else:
        assert hook["command"].endswith("run_hook.sh") and "args" not in hook


def test_uninstall_hook_leaves_other_settings(root):
    settings = root / "s.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/guard.sh"}]}]}}))
    main(["install-hook", "--settings", str(settings)])
    main(["uninstall-hook", "--settings", str(settings)])
    assert _hooks(settings) == [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/guard.sh"}]}]


def test_mod_config_uses_forward_slashes(root):
    assert main(["mod-config"]) == 0
    text = (root / "claude-mod" / "hooks" / "config.ts").read_text()
    run_dir = (root / "data" / "run").resolve().as_posix()
    assert f'export const RUN_DIR = "{run_dir}"' in text and "\\" not in text


def test_lock_is_exclusive(tmp_path):
    first = lock_file(tmp_path / "x.lock")
    with pytest.raises(LockHeld):
        lock_file(tmp_path / "x.lock")
    first.close()
    lock_file(tmp_path / "x.lock").close()


def _cli(root, *args, timeout=60):
    env = {**os.environ, "CLAUDE_OPTION_ROOT": str(root)}
    return subprocess.run([sys.executable, "-m", "claude_option.cli", *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_background_broker_start_status_stop_over_tcp(root):
    started = _cli(root, "start", "--no-telegram")
    assert started.returncode == 0, started.stderr + started.stdout
    try:
        assert "tcp" in started.stdout
        endpoint = json.loads((root / "data" / "run" / "endpoint.json").read_text())
        assert endpoint["transport"] == "tcp" and endpoint["host"] == "127.0.0.1"
        status = _cli(root, "status")
        assert status.returncode == 0 and '"transport": "tcp"' in status.stdout
        again = _cli(root, "start", "--no-telegram")
        assert "already running" in again.stdout
    finally:
        stopped = _cli(root, "stop")
    assert stopped.returncode == 0 and "stopped" in stopped.stdout
    assert _cli(root, "status").returncode == 1
    assert not (root / "data" / "run" / "endpoint.json").exists()
