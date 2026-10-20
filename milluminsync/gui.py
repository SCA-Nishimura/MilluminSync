"""CustomTkinter user interface.

The window is only looked at while setting up: during the show Millumin runs
full screen on both machines and MilluminSync keeps relaying in the background.
So the UI favours setup clarity, and the relay never waits on the UI.
"""

from __future__ import annotations

import queue
import socket
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk

from . import __version__
from .config import SYNC_BY_INDEX, SYNC_BY_NAME, Config, log_dir
from .diag import Diagnosis, diagnose_listen, diagnose_target, port_users
from .osc import OscMessage
from .relay import Relay, RelayEvent, Target

MAX_LOG_LINES = 500
MAX_PENDING_EVENTS = 2000
DIAG_INTERVAL_MS = 10_000

if sys.platform == "darwin":
    FONT, MONO = "Hiragino Sans", "Menlo"
elif sys.platform == "win32":
    FONT, MONO = "Yu Gothic UI", "Consolas"
else:
    FONT, MONO = "Noto Sans CJK JP", "DejaVu Sans Mono"

GREEN = "#2fbf71"
RED = "#e5484d"
MUTED = ("gray40", "gray60")
SYNC_LABELS = {SYNC_BY_INDEX: "番号で合わせる", SYNC_BY_NAME: "名前で合わせる"}
SYNC_HINTS = {
    SYNC_BY_INDEX: "Master と同じ番号の Column を起動します。両方の Column の並び順を揃えてください。",
    SYNC_BY_NAME: "Master と同じ名前の Column を起動します。並び順が違っても使えます。",
}


def local_ipv4_addresses() -> list[str]:
    ips: list[str] = []
    try:
        # Does not send anything; just asks the OS which interface it would use.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def font(size: int = 13, weight: str = "normal", mono: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO if mono else FONT, size=size, weight=weight)


class Card(ctk.CTkFrame):
    def __init__(self, master, title: str) -> None:
        super().__init__(master, corner_radius=10)
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=title, font=font(12), text_color=MUTED).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(10, 2))


class App:
    def __init__(self, root: ctk.CTk, config: Config, config_path: Path | None = None) -> None:
        self.root = root
        self.config = config
        self.config_path = config_path
        self.events: "queue.Queue[RelayEvent]" = queue.Queue(maxsize=MAX_PENDING_EVENTS)
        self.events_lost = 0
        self._log_file_failed = False
        self.relay: Relay | None = None
        self._diag_results: "queue.Queue[tuple[int, Diagnosis]]" = queue.Queue()
        self._diag_running = False
        self._diag_last: dict[int, Diagnosis] = {}
        self._applied: tuple[int, list[Target]] | None = None

        root.title(f"MilluminSync {__version__}")
        root.geometry("560x900")
        root.minsize(480, 720)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.var_port = ctk.StringVar(value=str(config.listen_port))
        self.var_target = ctk.StringVar(value=", ".join(config.targets))
        self.var_sync = ctk.StringVar(value=SYNC_LABELS[config.sync_by])
        self.var_stop = ctk.BooleanVar(value=config.sync_stop)
        self.var_autostart = ctk.BooleanVar(value=config.autostart)
        self.var_test = ctk.StringVar(value="1")

        self._build()
        self._update_guide()
        self.root.after(50, self._drain_events)
        self.root.after(1000, self._update_ignored)
        self.root.after(DIAG_INTERVAL_MS, self._diag_tick)
        if config.autostart:
            self.root.after(200, self.start)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        r = self.root
        r.grid_columnconfigure(0, weight=1)
        r.grid_rowconfigure(5, weight=1, minsize=170)

        # Header: status + start/stop
        head = ctk.CTkFrame(r, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.grid_columnconfigure(1, weight=1)
        self.dot = ctk.CTkLabel(head, text="●", font=font(20), text_color=RED, width=20)
        self.dot.grid(row=0, column=0, sticky="w")
        self.lbl_status = ctk.CTkLabel(head, text="停止中", font=font(18, "bold"))
        self.lbl_status.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.btn_start = ctk.CTkButton(head, text="開始", width=96, command=self.toggle, font=font(13))
        self.btn_start.grid(row=0, column=2, sticky="e")
        self.lbl_rx = ctk.CTkLabel(head, text="", font=font(12), text_color=MUTED)
        self.lbl_rx.grid(row=1, column=1, columnspan=2, sticky="w", padx=(6, 0))
        self.lbl_error = ctk.CTkLabel(r, text="", font=font(12), text_color=RED, wraplength=520, justify="left")
        self.lbl_error.grid(row=1, column=0, sticky="w", padx=18)

        # Connection
        c = Card(r, "接続")
        c.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        ctk.CTkLabel(c, text="受信ポート", font=font()).grid(row=1, column=0, sticky="w", padx=14, pady=4)
        e_port = ctk.CTkEntry(c, textvariable=self.var_port, width=90, font=font(13, mono=True))
        e_port.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=4)
        ctk.CTkLabel(c, text="Slave の Millumin", font=font()).grid(row=2, column=0, sticky="w", padx=14, pady=4)
        e_target = ctk.CTkEntry(c, textvariable=self.var_target, font=font(13, mono=True),
                                placeholder_text="127.0.0.1:12001")
        e_target.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=4)
        for e in (e_port, e_target):
            e.bind("<Return>", lambda _e: self._apply_connection())
            e.bind("<FocusOut>", lambda _e: self._apply_connection())

        guide = ctk.CTkFrame(c, corner_radius=8, fg_color=("gray86", "gray17"))
        guide.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(8, 12))
        guide.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(guide, text="Master の Millumin の OSC 送信先に入力", font=font(12),
                     text_color=MUTED).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.lbl_guide = ctk.CTkLabel(guide, text="", font=font(22, "bold", mono=True))
        self.lbl_guide.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
        self.lbl_guide_more = ctk.CTkLabel(guide, text="", font=font(11), text_color=MUTED)
        self.lbl_guide_more.grid(row=2, column=0, sticky="w", padx=12, pady=(0, 8))
        ctk.CTkButton(guide, text="コピー", width=64, font=font(12), command=self._copy_guide,
                      fg_color="transparent", border_width=1).grid(row=1, column=1, padx=12)

        # Sync
        s = Card(r, "同期")
        s.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        seg = ctk.CTkSegmentedButton(s, values=list(SYNC_LABELS.values()), variable=self.var_sync,
                                     command=lambda _v: self._apply_sync(), font=font())
        seg.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 2))
        self.lbl_sync_hint = ctk.CTkLabel(s, text="", font=font(12), text_color=MUTED,
                                          wraplength=480, justify="left")
        self.lbl_sync_hint.grid(row=2, column=0, columnspan=2, sticky="w", padx=14)
        ctk.CTkSwitch(s, text="Column の停止も同期する", variable=self.var_stop, font=font(),
                      command=self._apply_sync).grid(row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 2))
        ctk.CTkSwitch(s, text="アプリを開いたら自動で開始する", variable=self.var_autostart, font=font(),
                      command=self._save).grid(row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 12))
        self._apply_sync(save=False)

        # Last sync + test
        t = Card(r, "最後の同期")
        t.grid(row=4, column=0, sticky="ew", padx=16, pady=6)
        self.lbl_last = ctk.CTkLabel(t, text="まだありません", font=font(20, "bold"))
        self.lbl_last.grid(row=1, column=0, columnspan=3, sticky="w", padx=14)
        self.lbl_last_time = ctk.CTkLabel(t, text="Master で Column を起動するとここに表示されます",
                                          font=font(12), text_color=MUTED)
        self.lbl_last_time.grid(row=2, column=0, columnspan=3, sticky="w", padx=14)
        test = ctk.CTkFrame(t, fg_color="transparent")
        test.grid(row=3, column=0, columnspan=3, sticky="w", padx=14, pady=(8, 12))
        ctk.CTkLabel(test, text="Slave だけでテスト: Column", font=font()).pack(side="left")
        ctk.CTkEntry(test, textvariable=self.var_test, width=70, font=font(13, mono=True)).pack(side="left", padx=6)
        ctk.CTkButton(test, text="起動", width=64, font=font(), command=self.send_test).pack(side="left")
        ctk.CTkButton(test, text="ポートを診断", width=96, font=font(12), fg_color="transparent", border_width=1,
                      command=self.run_diagnosis).pack(side="left", padx=(8, 0))
        self.lbl_diag = ctk.CTkLabel(t, text="", font=font(12), text_color=MUTED, wraplength=500, justify="left")
        self.lbl_diag.grid(row=4, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 12))
        self.lbl_diag.grid_remove()

        # Log
        lg = Card(r, "ログ")
        lg.grid(row=5, column=0, sticky="nsew", padx=16, pady=(6, 14))
        lg.grid_rowconfigure(1, weight=1)
        self.log = ctk.CTkTextbox(lg, font=font(12, mono=True), wrap="none", height=120)
        self.log.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=10, pady=(2, 4))
        self.log.configure(state="disabled")
        for tag, color in (("in", "#4ea1ff"), ("rx", "#c9a227"), ("out", GREEN), ("error", RED), ("info", "gray55")):
            self.log.tag_config(tag, foreground=color)
        self.lbl_ignored = ctk.CTkLabel(lg, text="", font=font(11), text_color=MUTED, justify="left",
                                        wraplength=420)
        self.lbl_ignored.grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))
        btns = ctk.CTkFrame(lg, fg_color="transparent")
        btns.grid(row=2, column=2, sticky="e", padx=10, pady=(0, 10))
        ctk.CTkButton(btns, text="ログを保存した場所", width=120, font=font(12), fg_color="transparent",
                      border_width=1, command=self._open_log_dir).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btns, text="クリア", width=56, font=font(12), fg_color="transparent", border_width=1,
                      command=self._clear_log).pack(side="left")

    # ------------------------------------------------------------------ settings
    def _sync_by(self) -> str:
        return SYNC_BY_NAME if self.var_sync.get() == SYNC_LABELS[SYNC_BY_NAME] else SYNC_BY_INDEX

    def _apply_sync(self, save: bool = True) -> None:
        self.config.sync_by = self._sync_by()
        self.config.sync_stop = self.var_stop.get()
        self.lbl_sync_hint.configure(text=SYNC_HINTS[self.config.sync_by])
        if self.relay:
            self.relay.update(rules=self.config.rules())
        if save:
            self._save()

    def _parse_connection(self) -> tuple[int, list[Target]]:
        try:
            port = int(self.var_port.get().strip())
        except ValueError:
            raise ValueError("受信ポートには数字を入力してください (例: 12000)")
        if not 0 <= port <= 65535:
            raise ValueError("受信ポートは 0〜65535 で指定してください")
        try:
            targets = [Target.parse(t) for t in self.var_target.get().split(",") if t.strip()]
        except ValueError:
            raise ValueError("Slave の Millumin は「IP:ポート」の形で入力してください (例: 127.0.0.1:12001)")
        if not targets:
            raise ValueError("Slave の Millumin の送信先を入力してください (例: 127.0.0.1:12001)")
        for t in targets:
            if t.port == port and t.host in ("127.0.0.1", "localhost"):
                raise ValueError(f"送信先 {t} が受信ポートと同じです。Millumin の受信ポートとは別の番号にしてください")
        return port, targets

    def _apply_connection(self) -> None:
        """Restart the relay if the port or target changed while running."""
        self._update_guide()
        try:
            port, targets = self._parse_connection()
        except ValueError as e:
            self._show_error(str(e))
            return
        self._show_error("")
        if self.relay and self.relay.running and self._applied != (port, targets):
            if self.relay.listen_port == port:
                self.relay.update(targets=targets)
                self._applied = (port, targets)
                self._emit_info(f"送信先を変更: {', '.join(map(str, targets))}")
                self._save()
            else:
                self.stop()
                self.start()

    def _update_guide(self) -> None:
        ips = local_ipv4_addresses()
        port = self.var_port.get().strip() or "?"
        self.lbl_guide.configure(text=f"{ips[0]} : {port}")
        if len(ips) > 1:
            self.lbl_guide_more.configure(text="このPCの他のIP: " + ", ".join(ips[1:]))
            self.lbl_guide_more.grid()
        else:
            self.lbl_guide_more.grid_remove()

    def _copy_guide(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.lbl_guide.cget("text").replace(" ", ""))

    def _show_error(self, text: str) -> None:
        self.lbl_error.configure(text=text)

    # ------------------------------------------------------------------ relay
    def _on_relay_event(self, ev: RelayEvent) -> None:
        # Called from the relay thread. Never block it: if the UI falls behind
        # (window being dragged, app hidden...), drop log lines instead.
        try:
            self.events.put_nowait(ev)
        except queue.Full:
            self.events_lost += 1

    def toggle(self) -> None:
        if self.relay and self.relay.running:
            self.stop()
        else:
            self.start()

    def start(self) -> bool:
        try:
            port, targets = self._parse_connection()
        except ValueError as e:
            self._show_error(str(e))
            return False
        relay = Relay(port, targets, self.config.rules(), on_event=self._on_relay_event)
        try:
            relay.start()
        except OSError as e:
            who = diagnose_listen(port, port_users(port))
            self._show_error(
                f"ポート {port} を開けませんでした。{who or 'Millumin や Chataigne など、他のソフトが同じポートを使っていないか確認してください。'}"
                f" ({e})")
            return False
        self.relay = relay
        self._applied = (port, targets)
        self._show_error("")
        self.dot.configure(text_color=GREEN)
        self.lbl_status.configure(text=f"受信中  :{relay.listen_port}")
        self.btn_start.configure(text="停止", fg_color=("gray70", "gray30"))
        self._save()
        self.run_diagnosis()
        return True

    def stop(self) -> None:
        if self.relay:
            self.relay.stop()
        self.relay = None
        self._applied = None
        self.dot.configure(text_color=RED)
        self.lbl_status.configure(text="停止中")
        self.btn_start.configure(text="開始", fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"])

    def send_test(self) -> None:
        raw = self.var_test.get().strip()
        if not raw:
            return
        arg: int | str = int(raw) if raw.lstrip("-").isdigit() else raw
        try:
            _, targets = self._parse_connection()
        except ValueError as e:
            self._show_error(str(e))
            return
        sender = self.relay or Relay(0, targets, [], on_event=self._on_relay_event)
        sender.send(OscMessage("/millumin/action/launchColumn", [arg]), targets)
        # A UDP send "succeeds" even if nobody receives it, so check the port too.
        self.run_diagnosis(force_log=True)

    # ------------------------------------------------------------------ diagnosis
    def _local_target_ports(self) -> list[int]:
        try:
            _, targets = self._parse_connection()
        except ValueError:
            return []
        local = {"127.0.0.1", "localhost", *local_ipv4_addresses()}
        return sorted({t.port for t in targets if t.host in local})

    def run_diagnosis(self, force_log: bool = False) -> None:
        """Check, in the background, who owns the slave Millumin's port."""
        ports = self._local_target_ports()
        if not ports or self._diag_running:
            return
        self._diag_running = True
        self._diag_force_log = force_log

        def work() -> None:
            try:
                for p in ports:
                    self._diag_results.put((p, diagnose_target(p, port_users(p))))
            finally:
                self._diag_running = False

        threading.Thread(target=work, name="diag", daemon=True).start()

    def _diag_tick(self) -> None:
        # Re-check periodically: the failure is intermittent, so log when it changes.
        if self.relay and self.relay.running:
            self.run_diagnosis()
        self.root.after(DIAG_INTERVAL_MS, self._diag_tick)

    def _show_diagnosis(self, port: int, d: Diagnosis) -> None:
        prev = self._diag_last.get(port)
        self._diag_last[port] = d
        self.lbl_diag.configure(text=d.text, text_color=GREEN if d.ok else RED)
        self.lbl_diag.grid()
        if prev is None or prev.text != d.text or getattr(self, "_diag_force_log", False):
            self._emit_info(f"診断: {d.text}") if d.ok else self._on_relay_event(
                RelayEvent("error", f"診断: {d.text}", timestamp=time.time()))
        self._diag_force_log = False

    # ------------------------------------------------------------------ log
    def _emit_info(self, text: str) -> None:
        self._on_relay_event(RelayEvent("info", text, timestamp=time.time()))

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _drain_events(self) -> None:
        batch: list[RelayEvent] = []
        try:
            while len(batch) < 200:
                batch.append(self.events.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                self._show_diagnosis(*self._diag_results.get_nowait())
        except queue.Empty:
            pass
        if batch:
            self.log.configure(state="normal")
            file_lines = []
            for ev in batch:
                stamp = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
                prefix = {"in": "IN ", "rx": "IN ", "out": "OUT", "error": "ERR"}.get(ev.kind, "   ")
                text = ev.text
                if ev.kind in ("in", "rx") and ev.source:
                    text += f"  ← {ev.source.split(':')[0]}"
                if ev.kind == "rx":
                    text += "  (転送なし)"
                line = f"{stamp} {prefix} {text}"
                self.log.insert("end", line + "\n", ev.kind)
                file_lines.append(line)
                if ev.kind == "in" and ev.message and ev.address == "/millumin/board/launchedColumn":
                    self._show_last_sync(ev)
            excess = int(self.log.index("end-1c").split(".")[0]) - MAX_LOG_LINES
            if excess > 0:
                self.log.delete("1.0", f"{excess + 1}.0")
            self.log.configure(state="disabled")
            self.log.see("end")
            self._write_log_file(file_lines)
        self.root.after(50, self._drain_events)

    def _write_log_file(self, lines: list[str]) -> None:
        """Keep a daily log file so an intermittent failure can be looked at afterwards."""
        if not lines or self._log_file_failed:
            return
        try:
            path = log_dir() / time.strftime("%Y-%m-%d.log")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            self._log_file_failed = True

    def _open_log_dir(self) -> None:
        import subprocess
        path = log_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform == "win32":
                import os
                os.startfile(str(path))
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as e:
            self._emit_info(f"ログフォルダを開けませんでした: {path} ({e})")

    def _show_last_sync(self, ev: RelayEvent) -> None:
        args = ev.message.args if ev.message else []
        index = args[0] if args else "?"
        if isinstance(index, float) and index.is_integer():
            index = int(index)
        name = f"  {args[1]}" if len(args) > 1 and args[1] != "" else ""
        self.lbl_last.configure(text=f"Column {index}{name}")
        clock = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
        self.lbl_last_time.configure(text=f"{clock}  Master {ev.source.split(':')[0]} から受信")

    def _update_ignored(self) -> None:
        if self.relay and self.relay.last_rx_time:
            ago = max(0, int(time.time() - self.relay.last_rx_time))
            clock = time.strftime("%H:%M:%S", time.localtime(self.relay.last_rx_time))
            self.lbl_rx.configure(text=f"最終受信 {clock}（{ago} 秒前） ← {self.relay.last_rx_source}")
        elif self.relay:
            self.lbl_rx.configure(text="まだ何も受信していません")
        else:
            self.lbl_rx.configure(text="")
        if self.relay:
            ignored = self.relay.take_ignored()
            total = sum(ignored.values())
            if total:
                names = sorted(ignored, key=ignored.get, reverse=True)[:2]
                short = ", ".join(n.replace("/millumin/", "") for n in names)
                self.lbl_ignored.configure(text=f"転送なし {total} 通/秒  ({short} など)")
            else:
                self.lbl_ignored.configure(text="")
        if self.events_lost:
            self.lbl_ignored.configure(text=f"{self.lbl_ignored.cget('text')}  ログ省略 {self.events_lost} 行".strip())
            self.events_lost = 0
        self.root.after(1000, self._update_ignored)

    # ------------------------------------------------------------------ persistence
    def _save(self) -> None:
        try:
            self.config.listen_port = int(self.var_port.get().strip())
        except ValueError:
            pass
        targets = [t.strip() for t in self.var_target.get().split(",") if t.strip()]
        if targets:
            self.config.targets = targets
        self.config.autostart = self.var_autostart.get()
        try:
            self.config.save(self.config_path)
        except OSError as e:
            self._emit_info(f"設定を保存できませんでした: {e}")

    def on_close(self) -> None:
        self._save()
        if self.relay:
            self.relay.stop()
        self.root.destroy()


ASSETS = Path(__file__).resolve().parent / "assets"


def _set_window_icon(root: ctk.CTk) -> None:
    try:
        if sys.platform == "win32":
            import ctypes
            # Own taskbar entry/icon instead of grouping under python.exe.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MilluminSync")
            # Calling iconbitmap also stops CustomTkinter from applying its own icon.
            root.iconbitmap(str(ASSETS / "icon.ico"))
        else:
            import tkinter as tk
            root._icon_image = tk.PhotoImage(file=str(ASSETS / "icon-256.png"))
            root.iconphoto(True, root._icon_image)
    except Exception:
        pass  # a missing icon must never stop the app


def create_root() -> ctk.CTk:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    _set_window_icon(root)
    return root


def run(config_path: Path | None = None) -> None:
    config = Config.load(config_path)
    root = create_root()
    App(root, config, config_path)
    root.mainloop()
