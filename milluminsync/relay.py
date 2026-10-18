"""UDP OSC relay: listen on a port, apply rules, forward to one or more targets."""

from __future__ import annotations

import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .osc import OscError, OscMessage, decode_packet, encode_message
from .rules import Rule


@dataclass
class Target:
    host: str
    port: int

    @classmethod
    def parse(cls, text: str, default_port: int = 5000) -> "Target":
        text = text.strip()
        if not text:
            raise ValueError("empty target")
        if ":" in text:
            host, port = text.rsplit(":", 1)
            return cls(host.strip() or "127.0.0.1", int(port))
        return cls(text, default_port)

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class RelayEvent:
    """Something that happened inside the relay, for logging / UI."""
    kind: str  # "in", "out", "error", "info"
    text: str
    source: str = ""
    address: str = ""
    timestamp: float = 0.0
    message: OscMessage | None = None


EventCallback = Callable[[RelayEvent], None]


class Relay:
    def __init__(
        self,
        listen_port: int,
        targets: list[Target],
        rules: list[Rule],
        listen_host: str = "0.0.0.0",
        on_event: EventCallback | None = None,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.targets = list(targets)
        self.rules = list(rules)
        self.on_event = on_event
        self.received = 0
        self.forwarded = 0
        # Messages no rule matched (media time etc.), counted per address instead of
        # logged one by one: Millumin sends these every frame.
        self._ignored: dict[str, int] = {}
        self._sock: socket.socket | None = None
        self._send_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if sys.platform != "win32":
            # On Windows SO_REUSEADDR lets two programs share the port silently,
            # which would hide a conflict with Millumin / Chataigne.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.listen_host, self.listen_port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.2)
        self._sock = sock
        # Actual port (useful when listen_port == 0 in tests).
        self.listen_port = sock.getsockname()[1]
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="osc-relay", daemon=True)
        self._thread.start()
        targets = ", ".join(str(t) for t in self.targets) or "(none)"
        self._emit("info", f"Listening on {self.listen_host}:{self.listen_port} -> {targets}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        for s in (self._sock, self._send_sock):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
        self._sock = self._send_sock = None
        self._emit("info", "Stopped")

    def update(self, targets: list[Target] | None = None, rules: list[Rule] | None = None) -> None:
        """Change targets / rules while running."""
        with self._lock:
            if targets is not None:
                self.targets = list(targets)
            if rules is not None:
                self.rules = list(rules)

    # ------------------------------------------------------------------ sending
    def send(self, msg: OscMessage, targets: list[Target] | None = None) -> None:
        """Send a message directly to the targets (used for manual tests)."""
        sock = self._send_sock
        own = sock is None
        if own:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            data = encode_message(msg)
            with self._lock:
                dests = list(targets if targets is not None else self.targets)
            for t in dests:
                try:
                    sock.sendto(data, (t.host, t.port))
                    self.forwarded += 1
                    self._emit("out", f"{msg}  -> {t}", address=msg.address, message=msg)
                except OSError as e:
                    self._emit("error", f"send to {t} failed: {e}")
        finally:
            if own:
                sock.close()

    # ------------------------------------------------------------------ internals
    def process(self, msg: OscMessage) -> list[OscMessage]:
        """Apply every enabled rule; each matching rule produces one message."""
        with self._lock:
            rules = list(self.rules)
        out = []
        now = time.monotonic()
        for rule in rules:
            res = rule.apply(msg, now)
            if res is not None:
                out.append(res)
        return out

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as e:
                if self._stop.is_set():
                    break
                # Windows raises ConnectionResetError on ICMP port unreachable; ignore.
                if isinstance(e, ConnectionResetError):
                    continue
                self._emit("error", f"receive error: {e}")
                time.sleep(0.1)
                continue
            source = f"{addr[0]}:{addr[1]}"
            try:
                messages = list(decode_packet(data))
            except OscError as e:
                self._emit("error", f"bad packet from {source}: {e}", source=source)
                continue
            for msg in messages:
                self.received += 1
                outs = self.process(msg)
                if not outs:
                    with self._lock:
                        self._ignored[msg.address] = self._ignored.get(msg.address, 0) + 1
                    continue
                self._emit("in", str(msg), source=source, address=msg.address, message=msg)
                for o in outs:
                    self.send(o)

    def take_ignored(self) -> dict[str, int]:
        """Return and reset the per-address counts of messages that were not forwarded."""
        with self._lock:
            ignored, self._ignored = self._ignored, {}
        return ignored

    def _emit(self, kind: str, text: str, source: str = "", address: str = "",
              message: OscMessage | None = None) -> None:
        if self.on_event is not None:
            try:
                self.on_event(RelayEvent(kind, text, source, address, time.time(), message))
            except Exception:
                pass
