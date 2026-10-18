"""Load / save settings as JSON in the user's config directory."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rules import Rule, default_rules

APP_NAME = "MilluminSync"
DEFAULT_LISTEN_PORT = 8000
DEFAULT_TARGET = "127.0.0.1:5000"


def config_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def default_config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    listen_port: int = DEFAULT_LISTEN_PORT
    targets: list[str] = field(default_factory=lambda: [DEFAULT_TARGET])
    rules: list[Rule] = field(default_factory=default_rules)
    autostart: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "listen_port": self.listen_port,
            "targets": list(self.targets),
            "autostart": self.autostart,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        cfg = cls()
        cfg.listen_port = int(d.get("listen_port", DEFAULT_LISTEN_PORT))
        targets = d.get("targets", [DEFAULT_TARGET])
        if isinstance(targets, str):
            targets = [t for t in targets.split(",")]
        cfg.targets = [str(t).strip() for t in targets if str(t).strip()]
        cfg.autostart = bool(d.get("autostart", False))
        if "rules" in d:
            cfg.rules = [Rule.from_dict(r) for r in d["rules"]]
        return cfg

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or default_config_path()
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: Path | None = None) -> Path:
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return path
