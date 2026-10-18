"""Tkinter user interface."""

from __future__ import annotations

import json
import queue
import socket
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import __version__
from .config import Config
from .osc import OscMessage
from .relay import Relay, RelayEvent, Target
from .rules import Rule, default_rules

MAX_LOG_LINES = 2000


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


class RuleDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, rule: Rule | None) -> None:
        super().__init__(master)
        self.title("ルール編集")
        self.resizable(True, False)
        self.transient(master.winfo_toplevel())
        self.result: Rule | None = None
        rule = rule or Rule(match="/millumin/", name="New rule")

        self.var_name = tk.StringVar(value=rule.name)
        self.var_match = tk.StringVar(value=rule.match)
        self.var_send = tk.StringVar(value=rule.send)
        self.var_args = tk.StringVar(value="" if rule.args is None else json.dumps(rule.args, ensure_ascii=False))
        self.var_throttle = tk.StringVar(value=str(rule.throttle_ms))
        self.var_enabled = tk.BooleanVar(value=rule.enabled)

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        rows = [
            ("名前", self.var_name, ""),
            ("受信アドレス", self.var_match, "* = '/'以外の任意文字列, ** = 何でも"),
            ("送信アドレス", self.var_send, "空欄 = 受信と同じ。{1} = 1つ目の * の中身"),
            ("引数 (JSON)", self.var_args, '空欄 = そのまま転送。例: ["$0:i"]  ["$1:s"]  [3]  []'),
            ("間引き (ms)", self.var_throttle, "0 = 間引きなし"),
        ]
        for r, (label, var, hint) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r * 2, column=0, sticky="w", pady=(6, 0))
            ttk.Entry(frm, textvariable=var, width=56).grid(row=r * 2, column=1, sticky="ew", pady=(6, 0))
            if hint:
                ttk.Label(frm, text=hint, foreground="#777").grid(row=r * 2 + 1, column=1, sticky="w")
        ttk.Checkbutton(frm, text="有効", variable=self.var_enabled).grid(row=99, column=1, sticky="w", pady=6)
        frm.columnconfigure(1, weight=1)

        btns = ttk.Frame(frm)
        btns.grid(row=100, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="キャンセル", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()

    def _ok(self) -> None:
        try:
            match = self.var_match.get().strip()
            if not match.startswith("/"):
                raise ValueError("受信アドレスは / で始めてください")
            send = self.var_send.get().strip()
            if send and not send.startswith("/"):
                raise ValueError("送信アドレスは / で始めてください")
            args_text = self.var_args.get().strip()
            args = None
            if args_text:
                args = json.loads(args_text)
                if not isinstance(args, list):
                    raise ValueError('引数は JSON の配列で書いてください (例: ["$0"])')
            throttle = int(self.var_throttle.get().strip() or 0)
            self.result = Rule(
                name=self.var_name.get().strip(),
                match=match,
                send=send,
                args=args,
                throttle_ms=max(0, throttle),
                enabled=self.var_enabled.get(),
            )
        except (ValueError, json.JSONDecodeError) as e:
            messagebox.showerror("入力エラー", str(e), parent=self)
            return
        self.destroy()


class App:
    def __init__(self, root: tk.Tk, config: Config, config_path: Path | None = None) -> None:
        self.root = root
        self.config = config
        self.config_path = config_path
        self.events: "queue.Queue[RelayEvent]" = queue.Queue()
        self.relay: Relay | None = None

        root.title(f"MilluminSync {__version__}")
        root.geometry("920x680")
        root.minsize(720, 520)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.var_port = tk.StringVar(value=str(config.listen_port))
        self.var_targets = tk.StringVar(value=", ".join(config.targets))
        self.var_status = tk.StringVar(value="停止中")
        self.var_counts = tk.StringVar(value="受信 0 / 送信 0")
        self.var_show_in = tk.BooleanVar(value=True)
        self.var_show_drop = tk.BooleanVar(value=False)
        self.var_autostart = tk.BooleanVar(value=config.autostart)
        self.var_test_column = tk.StringVar(value="1")

        self._build()
        self._refresh_rules()
        self.root.after(50, self._drain_events)
        if config.autostart:
            self.root.after(200, self.start)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        top = ttk.LabelFrame(self.root, text="接続", padding=8)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="受信ポート").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_port, width=8).grid(row=0, column=1, sticky="w", padx=(4, 16))
        ttk.Label(top, text="送信先 (IP:ポート, カンマ区切りで複数可)").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.var_targets, width=40).grid(row=0, column=3, sticky="ew", padx=4)
        self.btn_start = ttk.Button(top, text="開始", command=self.toggle, width=10)
        self.btn_start.grid(row=0, column=4, padx=(8, 0))
        top.columnconfigure(3, weight=1)

        ips = ", ".join(local_ipv4_addresses())
        ttk.Label(top, text=f"このPCのIP: {ips}", foreground="#555").grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(top, text="起動時に自動開始", variable=self.var_autostart, command=self._save).grid(row=1, column=3, sticky="w", pady=(6, 0))
        self.lbl_status = ttk.Label(top, textvariable=self.var_status, foreground="#a00")
        self.lbl_status.grid(row=1, column=4, sticky="e", pady=(6, 0))

        # Rules -----------------------------------------------------------
        mid = ttk.LabelFrame(self.root, text="ルール (上から順に全て評価。一致したルールごとに1通送信)", padding=8)
        mid.pack(fill="both", expand=True, **pad)
        cols = ("on", "name", "match", "send", "args", "throttle")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=7, selectmode="browse")
        for c, text, w in [
            ("on", "有効", 44), ("name", "名前", 180), ("match", "受信アドレス", 220),
            ("send", "送信アドレス", 200), ("args", "引数", 90), ("throttle", "間引きms", 70),
        ]:
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, stretch=c not in ("on", "throttle"), anchor="center" if c in ("on", "throttle") else "w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.edit_rule())
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        rb = ttk.Frame(mid)
        rb.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for text, cmd in [
            ("追加", self.add_rule), ("編集", self.edit_rule), ("削除", self.delete_rule),
            ("有効/無効", self.toggle_rule), ("↑", lambda: self.move_rule(-1)), ("↓", lambda: self.move_rule(1)),
            ("初期ルールに戻す", self.reset_rules),
        ]:
            ttk.Button(rb, text=text, command=cmd).pack(side="left", padx=(0, 4))

        ttk.Button(rb, text="テスト送信", command=self.send_test).pack(side="right")
        ttk.Entry(rb, textvariable=self.var_test_column, width=5).pack(side="right", padx=4)
        ttk.Label(rb, text="launchColumn").pack(side="right")

        # Log ---------------------------------------------------------------
        bot = ttk.LabelFrame(self.root, text="ログ (受信行をダブルクリック → そのアドレスでルール作成)", padding=8)
        bot.pack(fill="both", expand=True, **pad)
        lb = ttk.Frame(bot)
        lb.pack(fill="x")
        ttk.Checkbutton(lb, text="受信を表示", variable=self.var_show_in).pack(side="left")
        ttk.Checkbutton(lb, text="ルール不一致を表示", variable=self.var_show_drop).pack(side="left", padx=8)
        ttk.Button(lb, text="クリア", command=lambda: self.log.delete("1.0", "end")).pack(side="right")
        ttk.Label(lb, textvariable=self.var_counts).pack(side="right", padx=8)

        lf = ttk.Frame(bot)
        lf.pack(fill="both", expand=True, pady=(4, 0))
        self.log = tk.Text(lf, height=10, wrap="none", font=("Menlo", 11) if self.root.tk.call("tk", "windowingsystem") == "aqua" else ("Consolas", 10))
        self.log.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        lsb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=lsb.set)
        self.log.tag_configure("in", foreground="#06c")
        self.log.tag_configure("out", foreground="#080")
        self.log.tag_configure("drop", foreground="#999")
        self.log.tag_configure("error", foreground="#c00")
        self.log.tag_configure("info", foreground="#555")
        self.log.bind("<Double-1>", self._log_double_click)

    # ------------------------------------------------------------------ relay
    def _parse_settings(self) -> tuple[int, list[Target]]:
        port = int(self.var_port.get().strip())
        if not 0 <= port <= 65535:
            raise ValueError("受信ポートは 0〜65535 で指定してください")
        targets = [Target.parse(t) for t in self.var_targets.get().split(",") if t.strip()]
        if not targets:
            raise ValueError("送信先を1つ以上指定してください (例: 127.0.0.1:5000)")
        for t in targets:
            if t.port == port and t.host in ("127.0.0.1", "localhost"):
                raise ValueError(f"送信先 {t} が受信ポートと同じです (ループします)")
        return port, targets

    def toggle(self) -> None:
        if self.relay and self.relay.running:
            self.stop()
        else:
            self.start()

    def start(self) -> bool:
        try:
            port, targets = self._parse_settings()
        except ValueError as e:
            messagebox.showerror("設定エラー", str(e), parent=self.root)
            return False
        self.relay = Relay(port, targets, self.config.rules, on_event=self.events.put)
        try:
            self.relay.start()
        except OSError as e:
            self.relay = None
            messagebox.showerror(
                "開始できません",
                f"ポート {port} を開けませんでした。\n他のソフト (Millumin / Chataigne など) が同じポートを使っていないか確認してください。\n\n{e}",
                parent=self.root,
            )
            return False
        self.var_status.set(f"動作中  :{self.relay.listen_port}")
        self.lbl_status.configure(foreground="#080")
        self.btn_start.configure(text="停止")
        self._save()
        return True

    def stop(self) -> None:
        if self.relay:
            self.relay.stop()
        self.var_status.set("停止中")
        self.lbl_status.configure(foreground="#a00")
        self.btn_start.configure(text="開始")

    def send_test(self) -> None:
        raw = self.var_test_column.get().strip()
        arg: int | str = int(raw) if raw.lstrip("-").isdigit() else raw
        try:
            _, targets = self._parse_settings()
        except ValueError as e:
            messagebox.showerror("設定エラー", str(e), parent=self.root)
            return
        sender = self.relay or Relay(0, targets, [], on_event=self.events.put)
        sender.send(OscMessage("/millumin/action/launchColumn", [arg]), targets)

    # ------------------------------------------------------------------ rules
    def _refresh_rules(self) -> None:
        sel = self._selected_index()
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.config.rules):
            args = "(そのまま)" if r.args is None else json.dumps(r.args, ensure_ascii=False)
            self.tree.insert("", "end", iid=str(i), values=(
                "●" if r.enabled else "", r.name, r.match, r.send or "(同じ)", args, r.throttle_ms or "",
            ))
        if sel is not None and sel < len(self.config.rules):
            self.tree.selection_set(str(sel))

    def _selected_index(self) -> int | None:
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _rules_changed(self) -> None:
        self._refresh_rules()
        if self.relay:
            self.relay.update(rules=self.config.rules)
        self._save()

    def add_rule(self, template: Rule | None = None) -> None:
        dlg = RuleDialog(self.root, template)
        self.root.wait_window(dlg)
        if dlg.result:
            self.config.rules.append(dlg.result)
            self._rules_changed()
            self.tree.selection_set(str(len(self.config.rules) - 1))

    def edit_rule(self) -> None:
        i = self._selected_index()
        if i is None:
            return
        dlg = RuleDialog(self.root, self.config.rules[i])
        self.root.wait_window(dlg)
        if dlg.result:
            self.config.rules[i] = dlg.result
            self._rules_changed()

    def delete_rule(self) -> None:
        i = self._selected_index()
        if i is None:
            return
        if messagebox.askyesno("削除", f"ルール「{self.config.rules[i].name}」を削除しますか？", parent=self.root):
            del self.config.rules[i]
            self.tree.selection_set(())
            self._rules_changed()

    def toggle_rule(self) -> None:
        i = self._selected_index()
        if i is None:
            return
        self.config.rules[i].enabled = not self.config.rules[i].enabled
        self._rules_changed()

    def move_rule(self, delta: int) -> None:
        i = self._selected_index()
        if i is None:
            return
        j = i + delta
        if 0 <= j < len(self.config.rules):
            rules = self.config.rules
            rules[i], rules[j] = rules[j], rules[i]
            self.tree.selection_set(str(j))
            self._rules_changed()
            self.tree.selection_set(str(j))

    def reset_rules(self) -> None:
        if messagebox.askyesno("初期ルール", "ルールを初期状態に戻しますか？", parent=self.root):
            self.config.rules = default_rules()
            self._rules_changed()

    # ------------------------------------------------------------------ log
    def _drain_events(self) -> None:
        lines = 0
        try:
            while lines < 500:
                ev = self.events.get_nowait()
                lines += 1
                if ev.kind == "in" and not self.var_show_in.get():
                    continue
                if ev.kind == "drop" and not self.var_show_drop.get():
                    continue
                stamp = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
                prefix = {"in": "IN ", "out": "OUT", "drop": "-- ", "error": "ERR", "info": "   "}.get(ev.kind, "   ")
                src = f" [{ev.source}]" if ev.source and ev.kind == "in" else ""
                self.log.insert("end", f"{stamp} {prefix} {ev.text}{src}\n", ev.kind)
        except queue.Empty:
            pass
        if lines:
            excess = int(self.log.index("end-1c").split(".")[0]) - MAX_LOG_LINES
            if excess > 0:
                self.log.delete("1.0", f"{excess + 1}.0")
            self.log.see("end")
        if self.relay:
            self.var_counts.set(f"受信 {self.relay.received} / 送信 {self.relay.forwarded}")
        self.root.after(50, self._drain_events)

    def _log_double_click(self, event: tk.Event) -> str:
        line = self.log.get(f"@{event.x},{event.y} linestart", f"@{event.x},{event.y} lineend")
        parts = line.split()
        address = next((p for p in parts if p.startswith("/")), None)
        if address:
            self.add_rule(Rule(match=address, send=address, name=address.rsplit("/", 1)[-1]))
        return "break"

    # ------------------------------------------------------------------ persistence
    def _save(self) -> None:
        try:
            self.config.listen_port = int(self.var_port.get().strip())
        except ValueError:
            pass
        self.config.targets = [t.strip() for t in self.var_targets.get().split(",") if t.strip()]
        self.config.autostart = self.var_autostart.get()
        try:
            self.config.save(self.config_path)
        except OSError as e:
            self.events.put(RelayEvent("error", f"設定を保存できませんでした: {e}", timestamp=time.time()))

    def on_close(self) -> None:
        self._save()
        if self.relay:
            self.relay.stop()
        self.root.destroy()


def run(config_path: Path | None = None) -> None:
    config = Config.load(config_path)
    root = tk.Tk()
    if root.tk.call("tk", "windowingsystem") == "x11":
        try:
            ttk.Style(root).theme_use("clam")
        except tk.TclError:
            pass
    App(root, config, config_path)
    root.mainloop()
