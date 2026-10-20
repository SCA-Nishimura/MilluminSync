"""Entry point.

    python -m milluminsync                 GUI
    python -m milluminsync --headless      run the relay in the terminal using the saved config
    python -m milluminsync --selftest      open the GUI, relay one message end-to-end, exit 0/1
"""

from __future__ import annotations

import argparse
import socket
import sys
import tempfile
import time
from pathlib import Path

from . import __version__
from .config import Config, default_config_path
from .osc import OscMessage, decode_packet, encode_message
from .relay import Relay, Target


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_headless(config_path: Path | None, listen_port: int | None, targets: list[str] | None) -> int:
    cfg = Config.load(config_path)
    port = listen_port if listen_port is not None else cfg.listen_port
    tgts = [Target.parse(t) for t in (targets or cfg.targets)]

    def log(ev):
        print(f"{time.strftime('%H:%M:%S')} {ev.kind.upper():5} {ev.text}", flush=True)

    relay = Relay(port, tgts, cfg.rules(), on_event=log)
    relay.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        relay.stop()
    return 0


def run_selftest(timeout: float = 10.0) -> int:
    """Start the real GUI, push a Millumin feedback message through it, check the output."""
    import tkinter as tk
    from .gui import App, create_root

    tmp = Path(tempfile.mkdtemp()) / "config.json"
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.setblocking(False)
    relay_port = _free_udp_port()

    cfg = Config(listen_port=relay_port, targets=[f"127.0.0.1:{receiver.getsockname()[1]}"], autostart=False)
    root = create_root()
    app = App(root, cfg, tmp)
    result = {"ok": False, "detail": "timeout"}
    deadline = time.monotonic() + timeout

    def kick() -> None:
        if not app.start():
            result["detail"] = "relay failed to start"
            root.destroy()
            return
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            dest = ("127.0.0.1", app.relay.listen_port)
            # Media time feedback must NOT reach the slave; only the column launch should.
            for t in (1.0, 1.02, 1.04):
                s.sendto(encode_message(OscMessage("/millumin/layer:Video/media/time", [t, 60.0])), dest)
            # An address no rule knows must still show up in the log (as "not forwarded").
            s.sendto(encode_message(OscMessage("/millumin/board/selftestUnknown", [1])), dest)
            s.sendto(encode_message(OscMessage("/millumin/board/launchedColumn", [3, "Intro"])), dest)
        root.after(20, poll)

    def poll() -> None:
        try:
            data, _ = receiver.recvfrom(65535)
        except (BlockingIOError, InterruptedError):
            if time.monotonic() > deadline:
                app.on_close()
            else:
                root.after(20, poll)
            return
        except OSError as e:
            # Windows may surface ICMP errors here; keep polling.
            result["detail"] = str(e)
            root.after(20, poll)
            return
        msgs = list(decode_packet(data))
        got = msgs[0] if msgs else None
        if got and got.address == "/millumin/action/launchColumn" and got.args == [3] and got.type_tags() == "i":
            result["detail"] = str(got)
            root.after(300, check_ui)
        else:
            result["detail"] = f"unexpected message: {got}"
            app.on_close()

    def check_ui() -> None:
        from .gui import ASSETS
        shown = app.lbl_last.cget("text")
        missing = [n for n in ("icon.ico", "icon-256.png") if not (ASSETS / n).exists()]
        log_text = app.log.get("1.0", "end")
        if shown != "Column 3  Intro":
            result["detail"] += f" / UI shows {shown!r}"
        elif "selftestUnknown" not in log_text or "(転送なし)" not in log_text:
            result["detail"] += " / unmatched message was not logged"
        elif missing:
            # Catches a packaged app built without its data files.
            result["detail"] += f" / missing assets {missing}"
        else:
            result["ok"] = True
        app.on_close()

    root.after(300, kick)
    root.mainloop()
    receiver.close()
    status = "PASS" if result["ok"] else "FAIL"
    print(f"selftest {status}: {result['detail']}  (Tk {tk.TkVersion}, Python {sys.version.split()[0]}, {sys.platform})")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="MilluminSync", description="OSC relay for syncing Millumin machines")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", type=Path, default=None, help=f"config file (default: {default_config_path()})")
    p.add_argument("--headless", action="store_true", help="run without GUI")
    p.add_argument("--port", type=int, default=None, help="listen port (headless)")
    p.add_argument("--target", action="append", default=None, help="IP:port to forward to (headless, repeatable)")
    p.add_argument("--selftest", action="store_true", help="GUI end-to-end self test, exits 0 on success")
    # macOS passes -psn_* when launched from Finder on old systems; ignore unknown args.
    args, _ = p.parse_known_args(argv)

    if args.selftest:
        return run_selftest()
    if args.headless:
        return run_headless(args.config, args.port, args.target)
    from .gui import run
    run(args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
