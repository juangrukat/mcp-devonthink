#!/usr/bin/env python3
"""Interactive connector manager for DEVONthink MCP on macOS."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = ROOT / "main.py"
CLIENTS = ("codex", "vscode", "claude", "hermes", "openclaw")
MANAGED_BEGIN = "# BEGIN DEVONTHINK MCP"
MANAGED_END = "# END DEVONTHINK MCP"


def _python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable or "python3"


def _env_block(profile: str) -> dict[str, str]:
    return {
        "PYTHONUNBUFFERED": "1",
        "DEVONTHINK_TOOL_PROFILE": profile,
    }


def _stdio_command(profile: str) -> dict[str, Any]:
    return {
        "command": _python_bin(),
        "args": [str(MAIN_PY), "--transport=stdio"],
        "env": _env_block(profile),
    }


def _json_load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _managed_toml(profile: str) -> str:
    py = _python_bin()
    env = _env_block(profile)
    return "\n".join(
        [
            MANAGED_BEGIN,
            "[mcp_servers.devonthink]",
            f'command = "{py}"',
            f'args = ["{MAIN_PY}", "--transport=stdio"]',
            "startup_timeout_sec = 25",
            "tool_timeout_sec = 180",
            "",
            "[mcp_servers.devonthink.env]",
            f'PYTHONUNBUFFERED = "{env["PYTHONUNBUFFERED"]}"',
            f'DEVONTHINK_TOOL_PROFILE = "{env["DEVONTHINK_TOOL_PROFILE"]}"',
            MANAGED_END,
            "",
        ]
    )


def _upsert_managed_block(path: Path, block: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    if MANAGED_BEGIN in text and MANAGED_END in text:
        start = text.index(MANAGED_BEGIN)
        end = text.index(MANAGED_END) + len(MANAGED_END)
        text = text[:start].rstrip() + "\n\n" + text[end:].lstrip()
    if text and not text.endswith("\n"):
        text += "\n"
    text += ("\n" if text.strip() else "") + block
    path.write_text(text)


def _target_paths(workspace: Path) -> dict[str, Path]:
    return {
        "codex": Path.home() / ".codex" / "config.toml",
        "vscode": workspace / ".vscode" / "mcp.json",
        "claude": Path.home() / ".claude" / "settings.json",
        "hermes": Path.home() / ".hermes" / "skills" / "devonthink-mcp.md",
        "openclaw": Path.home() / ".openclaw" / "openclaw.json",
    }


def _is_codex_configured(path: Path) -> bool:
    if not path.exists():
        return False
    return "[mcp_servers.devonthink]" in path.read_text()


def _is_vscode_configured(path: Path) -> bool:
    data = _json_load(path, {"servers": {}})
    return bool(((data.get("servers") or {}).get("devonthink")))


def _is_claude_configured(path: Path) -> bool:
    data = _json_load(path, {"mcpServers": {}})
    return bool(((data.get("mcpServers") or {}).get("devonthink")))


def _is_hermes_configured(path: Path) -> bool:
    if not path.exists():
        return False
    return "DEVONthink MCP" in path.read_text() and str(MAIN_PY) in path.read_text()


def _is_openclaw_configured(path: Path) -> bool:
    data = _json_load(path, {})
    servers = data.get("mcpServers") or []
    if isinstance(servers, dict):
        return "devonthink" in servers
    if isinstance(servers, list):
        return any(isinstance(item, dict) and item.get("name") == "devonthink" for item in servers)
    return False


def _running_server_pids() -> list[tuple[int, str]]:
    proc = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    matches: list[tuple[int, str]] = []
    main_str = str(MAIN_PY)
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or main_str not in line:
            continue
        pid_str, _, cmd = line.partition(" ")
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        matches.append((pid, cmd.strip()))
    return matches


def status(workspace: Path) -> dict[str, Any]:
    paths = _target_paths(workspace)
    configured = {
        "codex": _is_codex_configured(paths["codex"]),
        "vscode": _is_vscode_configured(paths["vscode"]),
        "claude": _is_claude_configured(paths["claude"]),
        "hermes": _is_hermes_configured(paths["hermes"]),
        "openclaw": _is_openclaw_configured(paths["openclaw"]),
    }
    return {
        "workspace": str(workspace),
        "python": _python_bin(),
        "main_py": str(MAIN_PY),
        "targets": {
            name: {"configured": configured[name], "path": str(paths[name])}
            for name in CLIENTS
        },
        "running_servers": [{"pid": pid, "command": cmd} for pid, cmd in _running_server_pids()],
    }


def install_codex(path: Path, profile: str) -> None:
    _upsert_managed_block(path, _managed_toml(profile))


def install_vscode(path: Path, profile: str) -> None:
    data = _json_load(path, {"servers": {}})
    data.setdefault("servers", {})
    data["servers"]["devonthink"] = {
        "type": "stdio",
        **_stdio_command(profile),
    }
    _json_dump(path, data)


def install_claude(path: Path, profile: str) -> None:
    data = _json_load(path, {"mcpServers": {}})
    data.setdefault("mcpServers", {})
    data["mcpServers"]["devonthink"] = _stdio_command(profile)
    _json_dump(path, data)


def install_hermes(path: Path, profile: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# DEVONthink MCP",
                "",
                "Use the DEVONthink MCP server for DEVONthink automation tasks.",
                "",
                "## Start the server",
                f"cd {ROOT}",
                f'{_python_bin()} {MAIN_PY} --transport=stdio',
                "",
                "## Recommended profile",
                f"- DEVONTHINK_TOOL_PROFILE={profile}",
                "",
                "## Key tools",
                "- devonthink-get-database-incoming-group",
                "- devonthink-list-group-children",
                "- devonthink-search-records",
                "- devonthink-get-record-by-uuid",
                "- devonthink-link-traverse-folder",
                "",
            ]
        )
    )


def install_openclaw(path: Path, profile: str) -> None:
    data = _json_load(path, {})
    payload = {
        "name": "devonthink",
        **_stdio_command(profile),
    }
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        servers["devonthink"] = payload
    else:
        if not isinstance(servers, list):
            servers = []
        kept = [item for item in servers if not (isinstance(item, dict) and item.get("name") == "devonthink")]
        kept.append(payload)
        data["mcpServers"] = kept
    _json_dump(path, data)


def install_targets(targets: list[str], workspace: Path, profile: str) -> None:
    paths = _target_paths(workspace)
    installers = {
        "codex": install_codex,
        "vscode": install_vscode,
        "claude": install_claude,
        "hermes": install_hermes,
        "openclaw": install_openclaw,
    }
    for target in targets:
        installers[target](paths[target], profile)


def stop_server() -> int:
    matches = _running_server_pids()
    for pid, _ in matches:
        os.kill(pid, signal.SIGTERM)
    return len(matches)


def print_status(payload: dict[str, Any]) -> None:
    print(f"Workspace: {payload['workspace']}")
    print(f"Python:    {payload['python']}")
    print(f"Server:    {payload['main_py']}")
    print()
    for name in CLIENTS:
        item = payload["targets"][name]
        mark = "[x]" if item["configured"] else "[ ]"
        print(f"{mark} {name:8s} {item['path']}")
    print()
    running = payload["running_servers"]
    if running:
        print("Running server processes:")
        for item in running:
            print(f"- pid {item['pid']}: {item['command']}")
    else:
        print("Running server processes: none")


def interactive_menu(workspace: Path, profile: str) -> int:
    while True:
        payload = status(workspace)
        print_status(payload)
        print()
        print("Actions:")
        print("1. Install/update connectors")
        print("2. Stop running DEVONthink MCP processes")
        print("3. Refresh status")
        print("4. Quit")
        choice = input("Choose an action [1-4]: ").strip()
        if choice == "1":
            print("Targets: codex, vscode, claude, hermes, openclaw")
            raw = input("Enter targets to install (comma-separated or 'all'): ").strip().lower()
            if raw == "all":
                targets = list(CLIENTS)
            else:
                targets = [part.strip() for part in raw.split(",") if part.strip()]
            invalid = [item for item in targets if item not in CLIENTS]
            if invalid:
                print(f"Invalid targets: {', '.join(invalid)}")
                print()
                continue
            install_targets(targets, workspace, profile)
            print(f"Installed/updated: {', '.join(targets)}")
            print()
        elif choice == "2":
            count = stop_server()
            print(f"Stopped {count} running DEVONthink MCP process(es).")
            print()
        elif choice == "3":
            print()
            continue
        elif choice == "4":
            return 0
        else:
            print("Invalid choice.")
            print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage DEVONthink MCP connectors on macOS.")
    parser.add_argument("command", nargs="?", default="interactive", choices=["interactive", "status", "install", "stop-server"])
    parser.add_argument("targets", nargs="*", choices=list(CLIENTS))
    parser.add_argument("--profile", default=os.environ.get("DEVONTHINK_TOOL_PROFILE", "canonical"))
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    profile = args.profile.strip().lower()
    if profile not in {"minimal", "canonical", "full"}:
        raise SystemExit(f"Invalid profile '{args.profile}'. Use minimal, canonical, or full.")

    if args.command == "interactive":
        return interactive_menu(workspace, profile)
    if args.command == "status":
        payload = status(workspace)
        if args.as_json:
            print(json.dumps(payload, indent=2))
        else:
            print_status(payload)
        return 0
    if args.command == "install":
        targets = list(args.targets) if args.targets else list(CLIENTS)
        install_targets(targets, workspace, profile)
        print(f"Installed/updated: {', '.join(targets)}")
        return 0
    if args.command == "stop-server":
        count = stop_server()
        print(f"Stopped {count} running DEVONthink MCP process(es).")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
