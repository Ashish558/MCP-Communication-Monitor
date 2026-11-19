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

    g = sub.add_parser("gateway", help="HTTP gateway for Claude Web (put a tunnel in front of it)")
    g.add_argument("--name", required=True, help="server name shown in popups")
    g.add_argument("--url", help="upstream HTTP MCP endpoint to protect (instead of a stdio command)")
    g.add_argument("--header", action="append", default=[], metavar="K=V",
                   help="extra header for the upstream URL, e.g. Authorization=Bearer ... (repeatable)")
    g.add_argument("--host", default="127.0.0.1", help="bind address (keep 127.0.0.1; the tunnel connects locally)")
    g.add_argument("--port", type=int, default=8765)
    g.add_argument("--public-url", help="the tunnel's https URL, used only to print the connector URL")
    g.add_argument("--label", default="claude-web", help="client label shown in popups")
    g.add_argument("--rotate-token", action="store_true", help="invalidate the old connector URL")
    g.add_argument("command", nargs=argparse.REMAINDER, help="-- <stdio server command...> (if no --url)")

    b = sub.add_parser("bridge", help="local endpoint for the claude.ai browser extension (Web connectors)")
    b.add_argument("--port", type=int, default=8766)
    b.add_argument("--rotate-token", action="store_true", help="invalidate the old extension token")

    sub.add_parser("hook", help="(internal) Claude Code PreToolUse hook: asks the popup before an MCP tool runs")
    for name, text in (("hook-install", "register the hook in a Claude Code settings file"),
                       ("hook-uninstall", "remove the hook from a Claude Code settings file"),
                       ("hook-status", "is the hook registered?")):
        h = sub.add_parser(name, help=text)
        h.add_argument("--target", required=True,
                       help='"user" (~/.claude/settings.json), "project" or "local" (./.claude/...), or a file path')
        if name != "hook-status":
            h.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
            h.add_argument("--backup-dir", type=Path)
        if name == "hook-install":
            h.add_argument("--matcher", default="mcp__.*",
                           help='which tools to gate (regex on the tool name; default "mcp__.*" = every MCP tool)')

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

    if args.cmd == "hook":
        from .hook import run_hook
        return run_hook()

    if args.cmd in ("hook-install", "hook-uninstall", "hook-status"):
        from . import installer
        try:
            path = installer.settings_target(args.target)
            if args.cmd == "hook-status":
                print(installer.hook_status(path))
            elif args.cmd == "hook-install":
                print(installer.hook_install(path, args.matcher, args.dry_run, args.backup_dir))
            else:
                print(installer.hook_uninstall(path, args.dry_run, args.backup_dir))
        except installer.InstallerError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "daemon":
        return _daemon(args)

    if args.cmd == "gateway":
        return _gateway(args)

    if args.cmd == "bridge":
        return _bridge(args)

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


def _gateway(args: argparse.Namespace) -> int:
    try:
        from aiohttp import web
    except ImportError:
        print('The gateway needs aiohttp:  pip install -e ".[gateway]"', file=sys.stderr)
        return 1
    from .gateway import create_app
    from .gateway.app import load_token
    from .gateway.upstream import HttpUpstream, StdioUpstream

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if bool(args.url) == bool(command):
        print("Give exactly one upstream: --url URL  or  -- <stdio command...>", file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("warning: binding to a non-local address exposes the gateway directly; "
              "use a tunnel in front of 127.0.0.1 instead.", file=sys.stderr)
    settings = Settings.load()
    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(home / "gateway.log"), level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.url:
        headers = dict(h.split("=", 1) for h in args.header if "=" in h)
        upstream = HttpUpstream(args.url, headers)
    else:
        upstream = StdioUpstream(command)
    token = load_token(home, rotate=args.rotate_token)
    app = create_app(upstream, settings, args.name, token, label=args.label)
    base = (args.public_url or f"http://{args.host}:{args.port}").rstrip("/")
    print(f"MCP Monitor gateway for {args.name!r} listening on {args.host}:{args.port}", flush=True)
    print(f"Connector URL (keep it secret): {base}/mcp/{token}", flush=True)
    if not args.public_url:
        print("Start a tunnel to the port above, then use <tunnel https URL>/mcp/<token> in claude.ai.", flush=True)
    # access_log=None: the access log would contain the secret path.
    # handler_cancellation: if Claude hangs up while a popup is open, the pending request is withdrawn.
    web.run_app(app, host=args.host, port=args.port, access_log=None, print=None, handler_cancellation=True)
    return 0


def _bridge(args: argparse.Namespace) -> int:
    try:
        from aiohttp import web
    except ImportError:
        print('The bridge needs aiohttp:  pip install -e ".[gateway]"', file=sys.stderr)
        return 1
    from .bridge import create_app, load_token

    settings = Settings.load()
    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(home / "bridge.log"), level=getattr(logging, settings.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    token = load_token(home, rotate=args.rotate_token)
    print(f"MCP Monitor bridge listening on 127.0.0.1:{args.port}", flush=True)
    print(f"Port: {args.port}\nToken (paste into the extension options): {token}", flush=True)
    web.run_app(create_app(settings, token), host="127.0.0.1", port=args.port, access_log=None, print=None,
                handler_cancellation=True)
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
    exit_code = 0
    try:
        presenter.run()
        logging.getLogger("mcp_monitor.daemon").info("daemon stopped normally (quit from the tray icon or stop request)")
    except KeyboardInterrupt:
        logging.getLogger("mcp_monitor.daemon").info("daemon stopped by Ctrl+C")
    except Exception:                       # noqa: BLE001 - pythonw has no console, so record it in the log
        logging.getLogger("mcp_monitor.daemon").exception("daemon crashed")
        exit_code = 1
    finally:
        daemon.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
