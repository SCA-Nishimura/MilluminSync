"""Address-mapping rules: turn an incoming OSC message into outgoing ones.

A rule looks like (JSON):

    {
      "name": "Column launch",
      "enabled": true,
      "match": "/millumin/board/launchedColumn",
      "send": "/millumin/action/launchColumn",
      "args": ["$0"],
      "throttle_ms": 0
    }

match   : OSC address pattern. "*" matches any run of characters except "/".
          "**" matches anything including "/".
send    : outgoing address. "{1}", "{2}" ... insert what the n-th "*" matched.
          Empty / omitted -> same address as the incoming message.
args    : outgoing arguments. Omitted -> pass all incoming args through.
          "$0", "$1" ...  incoming argument n (keeps its OSC type)
          "$*"            all incoming arguments
          "$0:i" / ":f" / ":s"  same, converted to int / float / string
          any other JSON value is sent literally (int, float, string, bool)
          A literal string that starts with "$" can be written as "$$...".
throttle_ms : at most one forward per this many milliseconds (0 = no limit).
              Messages arriving inside the window are dropped. Useful for
              high-rate values such as media time or faders.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from .osc import OscMessage


_ARG_REF = re.compile(r"^\$(\d+|\*)(?::([ifs]))?$")


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append("(.*)")
            i += 2
        elif pattern[i] == "*":
            out.append("([^/]*)")
            i += 1
        elif pattern[i] == "?":
            out.append("([^/])")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _convert(value: Any, tag: str, to: str | None) -> tuple[Any, str]:
    if to is None:
        return value, tag
    try:
        if to == "i":
            return int(float(value)), "i"
        if to == "f":
            return float(value), "f"
        if to == "s":
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value), "s"
    except (TypeError, ValueError):
        pass
    return value, tag


@dataclass
class Rule:
    match: str
    send: str = ""
    args: list[Any] | None = None
    name: str = ""
    enabled: bool = True
    throttle_ms: int = 0

    _regex: re.Pattern[str] | None = field(default=None, init=False, repr=False, compare=False)
    _last_sent: float = field(default=0.0, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._regex = _compile_pattern(self.match)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        if d["args"] is None:
            del d["args"]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Rule":
        return cls(
            match=str(d["match"]),
            send=str(d.get("send", "") or ""),
            args=list(d["args"]) if d.get("args") is not None else None,
            name=str(d.get("name", "")),
            enabled=bool(d.get("enabled", True)),
            throttle_ms=int(d.get("throttle_ms", 0) or 0),
        )

    def captures(self, address: str) -> tuple[str, ...] | None:
        assert self._regex is not None
        m = self._regex.match(address)
        return m.groups() if m else None

    def apply(self, msg: OscMessage, now: float | None = None) -> OscMessage | None:
        """Return the outgoing message, or None if the rule does not fire."""
        if not self.enabled:
            return None
        caps = self.captures(msg.address)
        if caps is None:
            return None
        if self.throttle_ms > 0:
            now = time.monotonic() if now is None else now
            if (now - self._last_sent) * 1000.0 < self.throttle_ms:
                return None
            self._last_sent = now

        address = self.send or msg.address
        for n, cap in enumerate(caps, start=1):
            address = address.replace("{%d}" % n, cap)

        if self.args is None:
            return OscMessage(address, list(msg.args), msg.type_tags())

        in_tags = msg.type_tags()
        out_args: list[Any] = []
        out_tags: list[str] = []
        for spec in self.args:
            if isinstance(spec, str):
                m = _ARG_REF.match(spec)
                if m:
                    idx, conv = m.group(1), m.group(2)
                    if idx == "*":
                        pairs = list(zip(msg.args, in_tags))
                    else:
                        i = int(idx)
                        if i >= len(msg.args):
                            # Referenced argument missing: rule cannot produce a valid message.
                            return None
                        pairs = [(msg.args[i], in_tags[i])]
                    for value, tag in pairs:
                        v, t = _convert(value, tag, conv)
                        out_args.append(v)
                        out_tags.append(t)
                    continue
                if spec.startswith("$$"):
                    spec = spec[1:]
            out_args.append(spec)
            out_tags.append(OscMessage("/", [spec]).type_tags())
        return OscMessage(address, out_args, "".join(out_tags))

