import os
import json

import pytest

from mcp_monitor import installer as inst

CONFIG = {
    "mcpServers": {
        "files": {"command": "npx", "args": ["-y", "server-filesystem", "C:/My Docs"], "env": {"K": "v"}},
        "bare": {"command": "mytool"},
        "remote": {"url": "https://example.com/mcp"},
    },
    "otherSetting": {"keep": True},
}


@pytest.fixture()
def cfg(tmp_path):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir()
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return path, tmp_path / "bk"


def test_wrap_and_unwrap_are_exact_inverses():
    for name in ("files", "bare"):
        entry = CONFIG["mcpServers"][name]
        assert inst.unwrap_entry(inst.wrap_entry(name, entry, "claude-desktop")) == entry


def test_install_wraps_only_chosen_servers_and_keeps_everything_else(cfg):
    path, bk = cfg
    inst.install(path, "claude-desktop", ["files"], backup_dir=bk)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert inst.is_wrapped(data["mcpServers"]["files"])
    assert data["mcpServers"]["files"]["env"] == {"K": "v"}
    assert data["mcpServers"]["bare"] == CONFIG["mcpServers"]["bare"]
    assert data["otherSetting"] == {"keep": True}


def test_install_all_skips_remote_servers(cfg):
    path, bk = cfg
    report = inst.install(path, "claude-desktop", None, backup_dir=bk)
    assert "remote" in report and "gateway" in report
    assert json.loads(path.read_text())["mcpServers"]["remote"] == CONFIG["mcpServers"]["remote"]


def test_dry_run_writes_nothing_and_makes_no_backup(cfg):
    path, bk = cfg
    before = path.read_text()
    report = inst.install(path, "claude-desktop", ["files"], dry_run=True, backup_dir=bk)
    assert "dry run" in report and path.read_text() == before and not bk.exists()


def test_backup_is_created_before_change_and_original_is_never_overwritten(cfg):
    path, bk = cfg
    inst.install(path, "claude-desktop", ["files"], backup_dir=bk)
    inst.install(path, "claude-desktop", ["bare"], backup_dir=bk)
    original = next(bk.glob("*.original.json"))
    assert json.loads(original.read_text()) == CONFIG          # still the pristine file
    assert len(list(bk.glob("*.json"))) >= 3


def test_install_twice_does_not_double_wrap(cfg):
    path, bk = cfg
    inst.install(path, "claude-desktop", ["files"], backup_dir=bk)
    once = path.read_text()
    assert "already wrapped" in inst.install(path, "claude-desktop", ["files"], backup_dir=bk)
    assert path.read_text() == once


def test_uninstall_restores_original_entries(cfg):
    path, bk = cfg
    inst.install(path, "claude-desktop", None, backup_dir=bk)
    inst.uninstall(path, None, backup_dir=bk)
    assert json.loads(path.read_text()) == CONFIG


def test_restore_puts_back_the_original_file(cfg):
    path, bk = cfg
    inst.install(path, "claude-desktop", None, backup_dir=bk)
    inst.restore(path, backup_dir=bk)
    assert json.loads(path.read_text()) == CONFIG


def test_bad_inputs_raise_clear_errors(tmp_path):
    with pytest.raises(inst.InstallerError):
        inst.load_config(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(inst.InstallerError):
        inst.load_config(bad)
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(CONFIG))
    with pytest.raises(inst.InstallerError):
        inst.install(ok, "claude-code", ["nope"], backup_dir=tmp_path / "bk")


def test_corrupt_config_is_left_untouched(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(inst.InstallerError):
        inst.install(bad, "claude-code", None, backup_dir=tmp_path / "bk")
    assert bad.read_text() == "{not json"


def test_list_and_doctor(cfg):
    path, bk = cfg
    assert dict(inst.list_servers(path))["files"] == "plain"
    assert any(line.startswith("WARN") for line in inst.doctor(path))
    inst.install(path, "claude-desktop", ["files"], backup_dir=bk)
    assert dict(inst.list_servers(path))["files"] == "wrapped"
    assert any("files: monitored" in line for line in inst.doctor(path))


@pytest.mark.skipif(os.name != "nt", reason="Microsoft Store layout is Windows only")
def test_desktop_config_path_finds_the_microsoft_store_package(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    # no Store package: the classic %APPDATA% location is used
    assert inst.desktop_config_path() == tmp_path / "roaming" / "Claude" / "claude_desktop_config.json"
    # Store package present: its private Roaming folder wins
    folder = tmp_path / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming" / "Claude"
    folder.mkdir(parents=True)
    assert inst.desktop_config_path() == folder / "claude_desktop_config.json"
    # Newer Store builds: the live config sits in Claude-Data and beats both
    live = tmp_path / "Claude-Data" / "claude_abc123"
    live.mkdir(parents=True)
    (live / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    assert inst.desktop_config_path() == live / "claude_desktop_config.json"
