"""Minimal OSC 1.0 encoder / decoder (no external dependencies).

Supports the argument types Millumin and most controllers use:
i (int32), f (float32), s (string), b (blob), h (int64), d (double),
T / F (bool), N (nil), plus #bundle containers.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Iterator

BUNDLE_TAG = b"#bundle\x00"


class OscError(ValueError):
    pass


@dataclass
class OscMessage:
    address: str
    args: list[Any] = field(default_factory=list)
    # Explicit type tags for each arg. When None, tags are inferred from Python types.
    types: str | None = None

    def type_tags(self) -> str:
        if self.types is not None and len(self.types) == len(self.args):
            return self.types
        return "".join(_infer_tag(a) for a in self.args)

    def __str__(self) -> str:
        if not self.args:
            return self.address
        parts = []
        for tag, a in zip(self.type_tags(), self.args):
            parts.append(f"{a!r}:{tag}" if tag in "s" else f"{a}:{tag}")
        return f"{self.address} " + " ".join(parts)


def _infer_tag(value: Any) -> str:
    if value is True:
        return "T"
    if value is False:
        return "F"
    if value is None:
        return "N"
    if isinstance(value, int):
        return "i" if -(2**31) <= value < 2**31 else "h"
    if isinstance(value, float):
        return "f"
    if isinstance(value, str):
        return "s"
    if isinstance(value, (bytes, bytearray)):
        return "b"
    raise OscError(f"unsupported OSC argument type: {type(value).__name__}")


def _pad4(n: int) -> int:
    return (4 - n % 4) % 4


def _encode_string(s: str) -> bytes:
    raw = s.encode("utf-8") + b"\x00"
    return raw + b"\x00" * _pad4(len(raw))


def _encode_blob(b: bytes) -> bytes:
    return struct.pack(">i", len(b)) + bytes(b) + b"\x00" * _pad4(len(b))


def encode_message(msg: OscMessage) -> bytes:
    if not msg.address.startswith("/"):
        raise OscError(f"OSC address must start with '/': {msg.address!r}")
    tags = msg.type_tags()
    out = [_encode_string(msg.address), _encode_string("," + tags)]
    for tag, a in zip(tags, msg.args):
        if tag == "i":
            out.append(struct.pack(">i", int(a)))
        elif tag == "f":
            out.append(struct.pack(">f", float(a)))
        elif tag == "s":
            out.append(_encode_string(str(a)))
        elif tag == "b":
            out.append(_encode_blob(a))
        elif tag == "h":
            out.append(struct.pack(">q", int(a)))
        elif tag == "d":
            out.append(struct.pack(">d", float(a)))
        elif tag in "TFN":
            pass
        else:
            raise OscError(f"unsupported type tag {tag!r}")
    return b"".join(out)


def _read_string(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise OscError("unterminated OSC string")
    s = data[pos:end].decode("utf-8", errors="replace")
    length = end - pos + 1
    return s, pos + length + _pad4(length)


def decode_message(data: bytes) -> OscMessage:
    address, pos = _read_string(data, 0)
    if not address.startswith("/"):
        raise OscError(f"invalid OSC address {address!r}")
    if pos >= len(data):
        # Some senders omit the type tag string entirely.
        return OscMessage(address, [], "")
    tags, pos = _read_string(data, pos)
    if not tags.startswith(","):
        raise OscError("missing OSC type tag string")
    tags = tags[1:]
    args: list[Any] = []
    kept_tags = []
    try:
        for tag in tags:
            if tag == "i":
                args.append(struct.unpack_from(">i", data, pos)[0]); pos += 4
            elif tag == "f":
                args.append(struct.unpack_from(">f", data, pos)[0]); pos += 4
            elif tag in "sS":
                s, pos = _read_string(data, pos); args.append(s); tag = "s"
            elif tag == "b":
                n = struct.unpack_from(">i", data, pos)[0]; pos += 4
                args.append(data[pos:pos + n]); pos += n + _pad4(n)
            elif tag == "h":
                args.append(struct.unpack_from(">q", data, pos)[0]); pos += 8
            elif tag == "d":
                args.append(struct.unpack_from(">d", data, pos)[0]); pos += 8
            elif tag == "T":
                args.append(True)
            elif tag == "F":
                args.append(False)
            elif tag in "NI":
                args.append(None); tag = "N"
            elif tag == "c":
                args.append(chr(struct.unpack_from(">i", data, pos)[0])); pos += 4; tag = "s"
            elif tag in "rmt":
                # rgba / midi / timetag: keep raw 4 or 8 bytes as int
                size = 8 if tag == "t" else 4
                fmt = ">Q" if size == 8 else ">I"
                args.append(struct.unpack_from(fmt, data, pos)[0]); pos += size
                tag = "h" if size == 8 else "i"
            else:
                raise OscError(f"unsupported type tag {tag!r}")
            kept_tags.append(tag)
    except struct.error as e:
        raise OscError(f"truncated OSC message: {e}") from e
    return OscMessage(address, args, "".join(kept_tags))


def decode_packet(data: bytes) -> Iterator[OscMessage]:
    """Yield every message in a packet, flattening nested bundles."""
    if data.startswith(BUNDLE_TAG):
        pos = len(BUNDLE_TAG) + 8  # skip timetag
        while pos + 4 <= len(data):
            size = struct.unpack_from(">i", data, pos)[0]
            pos += 4
            if size <= 0 or pos + size > len(data):
                raise OscError("malformed OSC bundle element")
            yield from decode_packet(data[pos:pos + size])
            pos += size
    else:
        yield decode_message(data)
