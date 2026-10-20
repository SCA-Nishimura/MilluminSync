"""Load / save settings as JSON in the user's config directory."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rules import Rule

APP_NAME = "MilluminSync"
DEFAULT_LISTEN_PORT = 12000
# Not 5000: macOS AirPlay Receiver (ControlCenter) also uses port 5000.
DEFAULT_TARGET = "127.0.0.1:12001"
SYNC_BY_INDEX = "index"
SYNC_BY_NAME = "name"


def config_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def log_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    return config_dir() / "logs"


def default_config_path() -> Path:
    return config_dir() / "config.json"


def build_rules(sync_by: str = SYNC_BY_INDEX, sync_stop: bool = False) -> list[Rule]:
    """Column-only sync, like a Chataigne Millumin setup.

    Media time and other feedback is never forwarded, so seeking on the master
    does not move the slave.
    """
    rules = [
        Rule(
            name="Column launch",
            match="/millumin/board/launchedColumn",
            send="/millumin/action/launchColumn",
            # launchedColumn carries (index, name). An int selects by index, a string by name.
            args=["$1:s"] if sync_by == SYNC_BY_NAME else ["$0:i"],
        ),
    ]
    if sync_stop:
        rules.append(Rule(
            name="Column stop",
            match="/millumin/board/stoppedColumn",
            send="/millumin/action/stopColumn",
            args=[],
        ))
    # Commands sent straight to MilluminSync (from a controller etc.) reach the slave unchanged.
    rules.append(Rule(name="Action pass-through", match="/millumin/action/**"))
    return rules


@dataclass
class Config:
    listen_port: int = DEFAULT_LISTEN_PORT
    targets: list[str] = field(default_factory=lambda: [DEFAULT_TARGET])
    sync_by: str = SYNC_BY_INDEX
    sync_stop: bool = False
    autostart: bool = True

    def rules(self) -> list[Rule]:
        return build_rules(self.sync_by, self.sync_stop)

    def to_dict(self) -> dict[str, Any]:
        return {
            "listen_port": self.listen_port,
            "targets": list(self.targets),
            "sync_by": self.sync_by,
            "sync_stop": self.sync_stop,
            "autostart": self.autostart,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        cfg = cls()
        cfg.listen_port = int(d.get("listen_port", DEFAULT_LISTEN_PORT))
        targets = d.get("targets", [DEFAULT_TARGET])
        if isinstance(targets, str):
            targets = targets.split(",")
        cfg.targets = [str(t).strip() for t in targets if str(t).strip()] or [DEFAULT_TARGET]
        sync_by = d.get("sync_by", SYNC_BY_INDEX)
        cfg.sync_by = sync_by if sync_by in (SYNC_BY_INDEX, SYNC_BY_NAME) else SYNC_BY_INDEX
        cfg.sync_stop = bool(d.get("sync_stop", False))
        cfg.autostart = bool(d.get("autostart", True))
        return cfg

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (OSError, ValueError, TypeError):
            # A broken config file must never stop the show; fall back to defaults.
            return cls()

    def save(self, path: Path | None = None) -> Path:
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return path
