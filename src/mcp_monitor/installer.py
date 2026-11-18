"""Install / uninstall the shim by editing Claude's MCP config files.

A config entry such as::

    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/data"]}

becomes::

    "files": {"command": "<python>", "args": ["-m", "mcp_monitor", "shim",
              "--server-name", "files", "--client", "claude-desktop", "--",
              "npx", "-y", "@modelcontextprotocol/server-filesystem", "C:/data"]}

The original command is everything after ``--``, so uninstalling needs no
side files. Safety rules:

* installing is opt-in per server (``--server`` / ``--all``);
* ``--dry-run`` shows the diff and writes nothing;
* a timestamped backup is written *before* every change, and the very first
  backup of a file (``<tag>.original.json``) is never overwritten;
* writes are atomic (temp file + rename) and the result is re-validated.
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from .config import project_backup_dir

MARKER = "mcp_monitor"


class InstallerError(Exception):
    pass


def desktop_config_path() -> Path:
    if os.name == "nt":
        # Newer Microsoft Store (MSIX) builds keep the live config under
        #   %LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json
        # older ones under %LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude, and
        # classic installs under %APPDATA%\Claude. Use the first that really exists.
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        for candidate in sorted((local / "Claude-Data").glob("*/claude_desktop_config.json")):
            return candidate
        for package in sorted((local / "Packages").glob("Claude_*")):
            folder = package / "LocalCache" / "Roaming" / "Claude"
            if folder.is_dir():
                return folder / "claude_desktop_config.json"
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def resolve_target(target: str) -> tuple[Path, str]:
    """``desktop`` -> Claude Desktop's config; anything else is a path (e.g. a project .mcp.json)."""
    if target.lower() == "desktop":
        return desktop_config_path(), "claude-desktop"
    return Path(target).expanduser().resolve(), "claude-code"


# --------------------------------------------------------------------------- config I/O
def load_config(path: Path) -> dict:
    if not path.is_file():
        hint = ""
        if path.name == "claude_desktop_config.json":
            hint = (" (open Claude Desktop > Settings > Developer > Edit Config once so it creates the file, "
                    "add your server under \"mcpServers\", then run this again)")
        raise InstallerError(f"config file not found: {path}{hint}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise InstallerError(f"cannot read {path} as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallerError(f"{path} does not contain a JSON object")
    return data


def servers_of(config: dict) -> dict:
    servers = config.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise InstallerError('"mcpServers" must be an object')
    return servers


def write_atomic(path: Path, data: dict) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(text)  # validate before touching the real file
    tmp = path.with_name(path.name + ".mcpmon.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _tag(path: Path) -> str:
    """Stable file-name prefix identifying which config a backup belongs to."""
    stem = "mcp" if path.name == ".mcp.json" else path.stem
    raw = f"{path.parent.name.lower()}_{stem}"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in raw)


def backup(path: Path, backup_dir: Path | None = None) -> Path:
    """Copy ``path`` into the backup folder; keep a never-overwritten ``.original`` copy too."""
    backup_dir = backup_dir or project_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    tag = _tag(path)
    original = backup_dir / f"{tag}.original.json"
    if not original.exists():
        shutil.copy2(path, original)
    stamped = backup_dir / f"{tag}.{time.strftime('%Y%m%d-%H%M%S')}.json"
    n = 1
    while stamped.exists():
        stamped = backup_dir / f"{tag}.{time.strftime('%Y%m%d-%H%M%S')}-{n}.json"
        n += 1
    shutil.copy2(path, stamped)
    return stamped


# --------------------------------------------------------------------------- entries
def is_wrapped(entry: dict) -> bool:
    args = entry.get("args")
    return isinstance(args, list) and MARKER in args and "shim" in args and "--" in args


def is_stdio(entry: object) -> bool:
    return isinstance(entry, dict) and isinstance(entry.get("command"), str) and "url" not in entry


def wrap_entry(name: str, entry: dict, client: str, python: str | None = None) -> dict:
    new = copy.deepcopy(entry)
    new["command"] = python or sys.executable
    new["args"] = ["-m", MARKER, "shim", "--server-name", name, "--client", client, "--",
                   entry["command"], *[str(a) for a in entry.get("args", [])]]
    return new


def unwrap_entry(entry: dict) -> dict:
    args = entry["args"]
    sep = args.index("--")
    original = [str(a) for a in args[sep + 1:]]
    if not original:
        raise InstallerError("wrapped entry has no original command")
    new = copy.deepcopy(entry)
    new["command"] = original[0]
    if original[1:]:
        new["args"] = original[1:]
    else:
        new.pop("args", None)
    return new


# --------------------------------------------------------------------------- operations
def list_servers(path: Path) -> list[tuple[str, str]]:
    """``(name, status)`` per server: wrapped / plain / remote (not wrappable by the shim)."""
    out = []
    for name, entry in servers_of(load_config(path)).items():
        if is_stdio(entry):
            out.append((name, "wrapped" if is_wrapped(entry) else "plain"))
        else:
            out.append((name, "remote (use the gateway)"))
    return out


def _diff(path: Path, before: dict, after: dict) -> str:
    a = json.dumps(before, indent=2, ensure_ascii=False).splitlines()
    b = json.dumps(after, indent=2, ensure_ascii=False).splitlines()
    return "\n".join(difflib.unified_diff(a, b, f"{path} (current)", f"{path} (new)", lineterm=""))


def install(path: Path, client: str, names: list[str] | None, dry_run: bool = False,
            backup_dir: Path | None = None) -> str:
    """Wrap the named servers (``None`` = every wrappable one). Returns a human-readable report."""
    config = load_config(path)
    servers = servers_of(config)
    wanted = list(servers) if names is None else names
    missing = [n for n in wanted if n not in servers]
    if missing:
        raise InstallerError(f"no such server(s) in {path}: {', '.join(missing)}")
    new_config = copy.deepcopy(config)
    report, changed = [], 0
    for name in wanted:
        entry = servers[name]
        if not is_stdio(entry):
            report.append(f"skip   {name}: remote/URL server (route it through the gateway instead)")
        elif is_wrapped(entry):
            report.append(f"skip   {name}: already wrapped")
        else:
            new_config["mcpServers"][name] = wrap_entry(name, entry, client)
            report.append(f"wrap   {name}")
            changed += 1
    if not changed:
        return "\n".join(report + ["Nothing to change."])
    if dry_run:
        return "\n".join(report + ["", _diff(path, config, new_config), "", "(dry run: nothing written)"])
    saved = backup(path, backup_dir)
    write_atomic(path, new_config)
    verify = servers_of(load_config(path))
    for name in wanted:
        if is_stdio(servers[name]) and not is_wrapped(servers[name]) and not is_wrapped(verify[name]):
            raise InstallerError(f"verification failed for {name}; restore with: mcp-monitor restore")
    return "\n".join(report + [f"Backup: {saved}", "Restart the Claude app so it reloads its MCP servers."])


def uninstall(path: Path, names: list[str] | None, dry_run: bool = False,
              backup_dir: Path | None = None) -> str:
    config = load_config(path)
    servers = servers_of(config)
    wanted = [n for n in servers if is_wrapped(servers[n])] if names is None else names
    new_config = copy.deepcopy(config)
    report, changed = [], 0
    for name in wanted:
        if name not in servers:
            raise InstallerError(f"no such server: {name}")
        if is_wrapped(servers[name]):
            new_config["mcpServers"][name] = unwrap_entry(servers[name])
            report.append(f"unwrap {name}")
            changed += 1
        else:
            report.append(f"skip   {name}: not wrapped")
    if not changed:
        return "\n".join(report + ["Nothing to change."])
    if dry_run:
        return "\n".join(report + ["", _diff(path, config, new_config), "", "(dry run: nothing written)"])
    saved = backup(path, backup_dir)
    write_atomic(path, new_config)
    return "\n".join(report + [f"Backup: {saved}", "Restart the Claude app so it reloads its MCP servers."])


def restore(path: Path, backup_file: Path | None = None, backup_dir: Path | None = None) -> str:
    """Put a backup back. Default: the pristine ``.original`` copy of this config."""
    backup_dir = backup_dir or project_backup_dir()
    if backup_file is None:
        candidate = backup_dir / f"{_tag(path)}.original.json"
        if not candidate.is_file():
            raise InstallerError(f"no original backup found at {candidate}; pass --from <file>")
        backup_file = candidate
    load_config(backup_file)  # must be valid JSON before it replaces anything
    if path.is_file():
        backup(path, backup_dir)  # keep what we are about to overwrite
    shutil.copy2(backup_file, path)
    return f"Restored {path} from {backup_file}. Restart the Claude app."


def doctor(path: Path) -> list[str]:
    """Health report: is each wrapped server still wrapped, is the python still there?"""
    lines = []
    try:
        servers = servers_of(load_config(path))
    except InstallerError as exc:
        return [f"ERROR {exc}"]
    for name, entry in servers.items():
        if not is_stdio(entry):
            lines.append(f"info  {name}: remote server, not covered by the shim")
        elif not is_wrapped(entry):
            lines.append(f"WARN  {name}: NOT monitored (plain entry)")
        elif not Path(entry["command"]).exists() and shutil.which(entry["command"]) is None:
            lines.append(f"ERROR {name}: shim python not found: {entry['command']}")
        else:
            lines.append(f"ok    {name}: monitored")
    return lines
