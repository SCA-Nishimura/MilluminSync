"""Find out which programs hold a port, to explain "the slave does not react".

The relay itself cannot tell whether a UDP packet was received: sendto() on
localhost succeeds even when nothing listens. So we ask the OS who owns the
port (lsof on macOS, netstat on Windows) and turn that into advice.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PortUser:
    proto: str     # "UDP" / "TCP"
    process: str
    pid: int


@dataclass
class Diagnosis:
    ok: bool
    text: str


def _run(cmd: list[str]) -> str | None:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout


def parse_lsof(output: str) -> list[PortUser]:
    """Parse `lsof +c 0 -nP -i...` output."""
    users: set[PortUser] = set()
    lines = output.splitlines()
    if not lines:
        return []
    header = lines[0].split()
    try:
        node_col = header.index("NODE")
    except ValueError:
        node_col = 7
    for line in lines[1:]:
        # COMMAND may contain spaces with +c 0; PID is the first purely numeric field after it.
        m = re.match(r"^(.*?)\s+(\d+)\s+(.*)$", line)
        if not m:
            continue
        rest = m.group(3).split()
        # rest = USER FD TYPE DEVICE SIZE/OFF NODE NAME...
        node_idx = node_col - 2
        proto = rest[node_idx] if 0 <= node_idx < len(rest) else ""
        if proto not in ("UDP", "TCP"):
            proto = "UDP" if "UDP" in rest else "TCP" if "TCP" in rest else "?"
        users.add(PortUser(proto, m.group(1).strip().replace("\\x20", " "), int(m.group(2))))
    return sorted(users, key=lambda u: (u.proto, u.process.lower(), u.pid))


def parse_netstat(output: str, port: int) -> list[tuple[str, int]]:
    """Parse `netstat -ano` output into (proto, pid) bound to the local port."""
    found: set[tuple[str, int]] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] not in ("UDP", "TCP"):
            continue
        local = parts[1]
        if not local.rsplit(":", 1)[-1] == str(port):
            continue
        if parts[0] == "TCP" and (len(parts) < 5 or parts[3] != "LISTENING"):
            continue
        try:
            found.add((parts[0], int(parts[-1])))
        except ValueError:
            continue
    return sorted(found)


def _windows_process_name(pid: int) -> str:
    out = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]) or ""
    m = re.match(r'^"([^"]+)"', out.strip())
    return m.group(1) if m else f"PID {pid}"


def port_users(port: int) -> list[PortUser] | None:
    """Programs bound to this port (UDP, or TCP listening). None = could not check."""
    if sys.platform == "win32":
        out = _run(["netstat", "-ano"])
        if out is None:
            return None
        return [PortUser(proto, _windows_process_name(pid), pid) for proto, pid in parse_netstat(out, port)]
    out = _run(["lsof", "+c", "0", "-nP", f"-iUDP:{port}", f"-iTCP:{port}", "-sTCP:LISTEN"])
    if out is None:
        return None
    return parse_lsof(out)


def _is_millumin(name: str) -> bool:
    return "millumin" in name.lower()


def _names(users: list[PortUser]) -> str:
    return ", ".join(sorted({f"{u.process} ({u.proto})" for u in users}))


def diagnose_target(port: int, users: list[PortUser] | None) -> Diagnosis:
    """Explain whether the slave Millumin can receive on this local port."""
    if users is None:
        return Diagnosis(True, f"ポート {port} の使用状況は確認できませんでした")
    airplay = [u for u in users if u.process.lower().startswith("controlce")]
    udp_millumin = [u for u in users if u.proto == "UDP" and _is_millumin(u.process)]
    others = [u for u in users if not _is_millumin(u.process)]
    if airplay:
        return Diagnosis(False,
            f"ポート {port} を macOS の AirPlay レシーバー (ControlCenter) が使っています。"
            f"Millumin の受信が不安定になります。Millumin の input port を別の番号 (例: 12001) に変え、"
            f"MilluminSync の「Slave の Millumin」も同じ番号にしてください。"
            f"または「システム設定 → 一般 → AirDrop と Handoff → AirPlay レシーバー」をオフにしてください。")
    if not udp_millumin and not others:
        return Diagnosis(False,
            f"ポート {port} で受信しているソフトがありません。Slave の Millumin で ⌘K → OSC → "
            f"OSC listener の input port が {port} になっているか、Millumin が起動しているか確認してください。")
    if others and udp_millumin:
        return Diagnosis(False,
            f"ポート {port} を Millumin 以外も使っています: {_names(others)}。"
            f"取り合いになり、Millumin に届かないことがあります。そのソフトを終了してください。")
    if others:
        return Diagnosis(False,
            f"ポート {port} を Millumin ではなく {_names(others)} が使っています。"
            f"そのソフトを終了し、Millumin の OSC listener を設定し直してください。")
    if len({u.pid for u in udp_millumin}) > 1:
        return Diagnosis(False,
            f"ポート {port} を Millumin が複数使っています (OSC listener が重複しているか、Millumin が2つ起動しています)。")
    return Diagnosis(True, f"ポート {port}: {udp_millumin[0].process} が受信しています ✓")


def diagnose_listen(port: int, users: list[PortUser] | None) -> str:
    """Explain why MilluminSync could not open its own port."""
    if not users:
        return ""
    return f"使用中のソフト: {_names(users)}"
