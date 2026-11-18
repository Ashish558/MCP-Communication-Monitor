"""Command line entry point: ``python -m mcp_monitor <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Settings, data_dir


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mcp-monitor", description="Allow/Block popup for MCP tool calls.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daemon", help="run the background approval app")
    d.add_argument("--auto", choices=["allow", "block"],
                   help="headless demo/CI mode: answer every request without a popup")
    d.add_argument("--no-tray", action="store_true", help="do not show a tray icon")

    sub.add_parser("status", help="is the daemon running?")

    def target_args(sp: argparse.ArgumentParser, servers: bool = True) -> None:
        sp.add_argument("--target", default="desktop",
                        help='"desktop" (Claude Desktop config) or a path such as a project .mcp.json')
        sp.add_argument("--backup-dir", type=Path, help="where backups go (default: <repo>/backups/config)")
        if servers:
            sp.add_argument("--server", action="append", dest="servers", metavar="NAME",
                            help="server to change (repeatable)")
            sp.add_argument("--all", action="store_true", help="change every server")
            sp.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")

    for name, text in (("install", "route servers through the monitor"),
                       ("uninstall", "put the original server commands back")):
        target_args(sub.add_parser(name, help=text))
    target_args(sub.add_parser("list", help="show servers and whether they are monitored"), servers=False)
    target_args(sub.add_parser("doctor", help="check the config"), servers=False)
    r = sub.add_parser("restore", help="restore a config from backup")
    target_args(r, servers=False)
    r.add_argument("--from", dest="backup_file", type=Path, help="specific backup file")

    s = sub.add_parser("shim", help="(internal) proxy in front of one MCP server")
    s.add_argument("--server-name", required=True)
    s.add_argument("--client", default="unknown")
    s.add_argument("command", nargs=argparse.REMAINDER, help="-- <real server command...>")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "shim":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            print("mcp-monitor shim: missing server command after --", file=sys.stderr)
            return 2
        from .shim import run_shim
        return run_shim(args.server_name, args.client, command)

    if args.cmd == "daemon":
        return _daemon(args)

    if args.cmd == "status":
        from .ipc import ping
        pong = ping()
        if pong:
            print(f"running, {pong.get('pending', 0)} request(s) pending (data dir: {data_dir()})")
            return 0
        print("not running")
        return 1

    from . import installer
    try:
        path, client = installer.resolve_target(args.target)
        if args.cmd == "list":
            for name, status in installer.list_servers(path):
                print(f"{status:28} {name}")
        elif args.cmd == "doctor":
            lines = installer.doctor(path)
            print("\n".join(lines))
            return 1 if any(line.startswith("ERROR") for line in lines) else 0
        elif args.cmd == "restore":
            print(installer.restore(path, args.backup_file, args.backup_dir))
        else:
            if not args.servers and not args.all:
                print("Choose servers with --server NAME (repeatable) or --all.", file=sys.stderr)
                return 2
            names = None if args.all else args.servers
            fn = installer.install if args.cmd == "install" else installer.uninstall
            kwargs = {"client": client} if args.cmd == "install" else {}
            print(fn(path, names=names, dry_run=args.dry_run, backup_dir=args.backup_dir, **kwargs))
    except installer.InstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _daemon(args: argparse.Namespace) -> int:
    from .daemon.runtime import AlreadyRunning, Daemon

    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    settings = Settings.load(home)
    logging.basicConfig(filename=str(home / "daemon.log"), level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.auto:
        from .daemon.scripted import ScriptedPresenter
        verdict = args.auto
        presenter = ScriptedPresenter(lambda req: (verdict, f"auto-{verdict} (headless mode)"))
    else:
        from .daemon.ui import TkPresenter
        presenter = TkPresenter(settings, use_tray=not args.no_tray)
    daemon = Daemon(presenter, settings, home)
    try:
        daemon.start()
    except AlreadyRunning:
        print("MCP Monitor daemon is already running.", file=sys.stderr)
        return 0
    try:
        presenter.run()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
