"""Claude Code PreToolUse hook: decisions, failure handling, and the settings-file installer."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import write_config
from mcp_monitor import installer as inst
from mcp_monitor.config import Settings
from mcp_monitor.hook import handle_payload, split_mcp_tool


def payload(tool="mcp__claude_ai_Figma__whoami", args=None, **extra):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args if args is not None else {},
            "tool_use_id": "toolu_1", **extra}


# --------------------------------------------------------------------------- decisions
def test_non_mcp_tools_are_not_touched(daemon, home):
    result = handle_payload(payload("Bash", {"command": "ls"}), Settings.load(home))
    assert (result.exit_code, result.stdout, result.stderr) == (0, "", "")
    assert daemon.presenter.shown == []                       # no popup for ordinary tools


def test_allowed_mcp_call_returns_the_documented_allow_json(daemon, home):
    result = handle_payload(payload(args={"note": "allow-me"}), Settings.load(home))
    assert result.exit_code == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "allow"


def test_blocked_mcp_call_exits_2_with_a_reason_for_claude(daemon, home):
    result = handle_payload(payload(args={"note": "block-me"}), Settings.load(home))
    assert result.exit_code == 2 and result.stdout == ""
    assert "Blocked by MCP Monitor" in result.stderr


def test_popup_shows_server_tool_parameters_and_the_hook_client(daemon, home):
    handle_payload(payload("mcp__srv__get_design", {"fileKey": "allow-me ABC"},
                           mcp_server={"name": "Figma", "source": "user"}), Settings.load(home))
    shown = daemon.presenter.shown[0]
    assert (shown.server, shown.tool, shown.client) == ("Figma", "get_design", "claude-code (hook)")
    assert "ABC" in shown.arguments_preview


def test_server_name_falls_back_to_the_tool_prefix(daemon, home):
    handle_payload(payload("mcp__5d2a533c-uuid__whoami", {"x": "allow-me"}), Settings.load(home))
    assert daemon.presenter.shown[0].server == "5d2a533c-uuid"


def test_connector_ids_get_a_readable_label(daemon, home):
    # Claude Desktop names claude.ai connectors by id: mcp__<uuid>__<tool>
    uuid = "5d2a533c-5b1d-4420-8bd4-582ca09d0d8c"
    handle_payload(payload(f"mcp__{uuid}__whoami", {"x": "allow-me"}), Settings.load(home))
    assert daemon.presenter.shown[0].server == "claude.ai connector (5d2a533c)"


def test_secrets_in_tool_input_are_masked_in_the_popup(daemon, home):
    handle_payload(payload(args={"api_key": "sk-abcdefghijklmnopqrstuv", "note": "allow-me"}), Settings.load(home))
    assert "sk-abcdefghijklmnopqrstuv" not in daemon.presenter.shown[0].arguments_preview


def test_skip_servers_setting_leaves_a_covered_server_alone(daemon, home):
    write_config(home, decision_timeout=3, hook_skip_servers=["fakefiles"])
    result = handle_payload(payload("mcp__fakefiles__echo", {"text": "hi"}), Settings.load(home))
    assert result.exit_code == 0 and result.stdout == "" and daemon.presenter.shown == []


def test_disable_switch_makes_the_hook_a_no_op(daemon, home, monkeypatch):
    monkeypatch.setenv("MCP_MONITOR_DISABLE", "1")
    assert handle_payload(payload(args={"n": "block-me"}), Settings.load(home)).exit_code == 0


# --------------------------------------------------------------------------- failures all block
def test_unanswered_popup_blocks_with_exit_2(daemon, home):
    result = handle_payload(payload(args={"n": "ignore-me"}), Settings.load(home))
    assert result.exit_code == 2 and "No answer" in result.stderr


def test_daemon_down_blocks_with_exit_2(home):
    write_config(home, decision_timeout=2)                    # no daemon, autostart off
    result = handle_payload(payload(args={"n": "x"}), Settings.load(home))
    assert result.exit_code == 2 and "not running" in result.stderr


@pytest.mark.parametrize("bad", [None, "text", 42, ["a"]])
def test_unreadable_input_blocks(daemon, home, bad):
    assert handle_payload(bad, Settings.load(home)).exit_code == 2


def test_unexpected_error_inside_the_hook_blocks(daemon, home):
    def boom(_name):
        raise RuntimeError("boom")

    assert handle_payload(payload(), Settings.load(home), gate_factory=boom).exit_code == 2


def test_tool_name_parsing():
    assert split_mcp_tool("mcp__figma__get_metadata") == ("figma", "get_metadata")
    assert split_mcp_tool("mcp__srv__tool__with__more") == ("srv", "tool__with__more")
    assert split_mcp_tool("Bash") is None and split_mcp_tool("mcp__lonely") == ("lonely", "lonely")


# --------------------------------------------------------------------------- the real process
def _run_hook(home, stdin_text):
    env = {**os.environ, "MCP_MONITOR_HOME": str(home)}
    return subprocess.run([sys.executable, "-m", "mcp_monitor", "hook"], input=stdin_text.encode(),
                          capture_output=True, env=env, timeout=30)


def test_real_hook_process_allow_block_and_garbage(daemon, home):
    ok = _run_hook(home, json.dumps(payload(args={"n": "allow-me"})))
    assert ok.returncode == 0 and b'"permissionDecision": "allow"' in ok.stdout
    no = _run_hook(home, json.dumps(payload(args={"n": "block-me"})))
    assert no.returncode == 2 and b"Blocked by MCP Monitor" in no.stderr
    bad = _run_hook(home, "this is not json")
    assert bad.returncode == 2


# --------------------------------------------------------------------------- installer
@pytest.fixture()
def settings_file(tmp_path):
    p = tmp_path / ".claude" / "settings.json"
    p.parent.mkdir()
    p.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}), encoding="utf-8")
    return p


def _hooks(path):
    return json.loads(path.read_text(encoding="utf-8"))["hooks"]


def test_hook_install_adds_ours_and_keeps_everything_else(settings_file, tmp_path):
    inst.hook_install(settings_file, backup_dir=tmp_path / "bk")
    data = json.loads(settings_file.read_text(encoding="utf-8"))
    assert data["model"] == "x" and "Stop" in data["hooks"]
    groups = data["hooks"]["PreToolUse"]
    assert groups[0]["hooks"][0]["command"] == "echo mine"             # the user's own hook is untouched
    ours = groups[1]
    assert ours["matcher"] == "mcp__.*"
    handler = ours["hooks"][0]
    assert handler["onFailure"] == "block" and handler["timeout"] == 100
    assert handler["command"].endswith("-m mcp_monitor hook")
    assert list((tmp_path / "bk").glob("*.original.json"))              # backup made first


def test_hook_install_is_idempotent_and_can_change_the_matcher(settings_file, tmp_path):
    inst.hook_install(settings_file, backup_dir=tmp_path / "bk")
    inst.hook_install(settings_file, matcher="mcp__(?!fakefiles__).*", backup_dir=tmp_path / "bk")
    ours = [g for g in _hooks(settings_file)["PreToolUse"] if any(inst._is_our_hook(h) for h in g["hooks"])]
    assert len(ours) == 1 and ours[0]["matcher"] == "mcp__(?!fakefiles__).*"


def test_hook_dry_run_writes_nothing(settings_file):
    before = settings_file.read_text(encoding="utf-8")
    report = inst.hook_install(settings_file, dry_run=True)
    assert "dry run" in report and settings_file.read_text(encoding="utf-8") == before


def test_hook_uninstall_removes_only_ours(settings_file, tmp_path):
    original = json.loads(settings_file.read_text(encoding="utf-8"))
    inst.hook_install(settings_file, backup_dir=tmp_path / "bk")
    inst.hook_uninstall(settings_file, backup_dir=tmp_path / "bk")
    assert json.loads(settings_file.read_text(encoding="utf-8")) == original
    assert "No MCP Monitor hook" in inst.hook_uninstall(settings_file, backup_dir=tmp_path / "bk")


def test_hook_install_creates_a_missing_settings_file_and_cleans_up_after(tmp_path):
    path = tmp_path / "proj" / ".claude" / "settings.json"
    inst.hook_install(path, backup_dir=tmp_path / "bk")
    assert inst.hook_status(path).startswith("installed")
    inst.hook_uninstall(path, backup_dir=tmp_path / "bk")
    assert json.loads(path.read_text(encoding="utf-8")) == {}           # no empty hooks left behind


def test_hook_install_refuses_a_corrupt_settings_file(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{not json")
    with pytest.raises(inst.InstallerError):
        inst.hook_install(bad, backup_dir=tmp_path / "bk")
    assert bad.read_text() == "{not json"
