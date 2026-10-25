# -*- coding: utf-8 -*-
"""等待回复时的对话修仙小游戏。

用法：pythonw xiuxian.py            常驻托盘，检测到 LLM 回复时自动弹出
      python  xiuxian.py --demo     不检测，直接弹出（试玩）
      python  xiuxian.py --data DIR 指定存档目录（默认与脚本同目录）
"""
import argparse
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import detector
import game as gm
import tray as tr

HERE = os.path.dirname(os.path.abspath(__file__))
# 字体已经按高分屏提高，窗口也要同步留出正文和三枚选项的空间。
# 600×900 给中段剧情留出更充足的纵向空间；在常见 1080p 桌面上仍能
# 完整停在右下角，三枚选项与底部提示保持原有大小。
W, H = 600, 900
# 用户在回复刚开始时主动关掉窗口，通常表示此刻不想被打扰；在一小时内
# 忽略自动弹出，仍可从托盘手动打开。阈值只针对“很快关闭”，避免误触
# 关闭长时间已经使用的窗口也屏蔽后续提醒。
DISMISS_QUICK_SECONDS = 30.0
DISMISS_COOLDOWN_SECONDS = 3600.0
BG, PANEL, FG, DIM, ACC = "#1b1d23", "#23262e", "#f1eddf", "#b3b8c2", "#e2c47b"
BTN, BTN_H = "#2d313c", "#3b4150"
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = -1, 2, 1, 0x10


def auto_popup_allowed(suppress_until, now=None):
    """返回自动弹窗是否已解除抑制；托盘手动打开不经过此判断。"""
    now = time.time() if now is None else now
    try:
        return now >= float(suppress_until or 0)
    except (TypeError, ValueError):
        return True


def prepare_dpi():
    """必须在第一个 Tk 窗口创建前调用；已设 DPI 的宿主不重复修改。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def single_instance(demo=False):
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    h = k.CreateMutexW(None, False, "Local\\XiuxianThinkingGame" + ("Demo" if demo else ""))
    return ctypes.windll.kernel32.GetLastError() != 183, h  # 183 = ALREADY_EXISTS


class App:
    def __init__(self, data_dir, demo=False):
        self.demo = demo
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.game = gm.Game(os.path.join(data_dir, "save.json"))
        self.game.load()
        self.prefix = self.game.welcome()
        self.q = queue.Queue()
        self.paused = False
        self.visible = False
        self.state = "idle"  # idle / thinking / rest
        self.stop = threading.Event()
        self._cfg_job = None
        self.page = "scene"  # scene / result / history
        self.session_rounds = 0
        ui = self.game.s.get("ui", {})
        self.enabled = frozenset(ui.get("enabled_tools", detector.ADAPTERS))
        try:
            self.suppress_until = float(ui.get("suppress_until", 0) or 0)
        except (TypeError, ValueError):
            self.suppress_until = 0.0
        self._shown_at = None
        self._auto_shown = False

        prepare_dpi()
        self._build()
        self._place()
        self._render()

        icon = os.path.join(data_dir, "icon.ico")
        try:
            tr.make_icon_file(icon)
            self.tray = tr.Tray("修仙小游戏", icon, self._menu, lambda c: self.q.put(c))
            self.tray.start()
        except Exception:
            self.tray = None

        if not demo:
            threading.Thread(target=self._watch, name="watch", daemon=True).start()
        self.root.after(150, self._pump)
        if demo:
            self.root.after(300, lambda: self._on_start())

    # ------------------------------------------------------------ 界面
    def _build(self):
        r = self.root = tk.Tk()
        r.title("修仙")
        r.configure(bg=BG)
        r.attributes("-topmost", True)
        r.attributes("-alpha", 0.0)  # 先透明，避免创建时闪一下抢焦点
        r.resizable(False, False)
        r.protocol("WM_DELETE_WINDOW", self.hide)
        fam = "Microsoft YaHei UI"
        # geometry 使用稳定的逻辑像素，避免在 100% DPI 下被 Tk scaling 缩成
        # 315×390；SetProcessDpiAwareness 已交给 Windows 处理高分屏映射。
        self.scale = 1.0
        self.px = lambda n: n
        self.width, self.height = W, H
        self.f_head = tkfont.Font(family=fam, size=12)
        self.f_realm = tkfont.Font(family=fam, size=19, weight="bold")
        self.f_body = tkfont.Font(family=fam, size=15)
        self.f_btn = tkfont.Font(family=fam, size=13)
        self.f_small = tkfont.Font(family=fam, size=11)

        top = tk.Frame(r, bg=PANEL)
        top.pack(fill="x", side="top")
        self.l_title = tk.Label(top, bg=PANEL, fg=DIM, font=self.f_head, anchor="w")
        self.l_realm = tk.Label(top, bg=PANEL, fg=ACC, font=self.f_realm, anchor="w")
        self.l_prog = tk.Label(top, bg=PANEL, fg=FG, font=self.f_small, anchor="w")
        self.l_stat = tk.Label(top, bg=PANEL, fg=DIM, font=self.f_small, anchor="w")
        for lb in (self.l_title, self.l_realm, self.l_prog, self.l_stat):
            lb.pack(fill="x", padx=self.px(14), pady=self.px(1))

        bar = tk.Frame(r, bg=BG)
        bar.pack(fill="x", padx=self.px(14), pady=(self.px(6), 0))
        self.l_chapter = tk.Label(bar, bg=BG, fg=ACC, font=self.f_head, anchor="w")
        self.l_chapter.pack(side="left")
        tk.Button(bar, text="前情", font=self.f_small, bg=BG, fg=DIM, relief="flat",
                  command=self.history, takefocus=False).pack(side="right")

        self.bottom = tk.Frame(r, bg=BG)
        self.bottom.pack(fill="x", side="bottom", padx=self.px(12), pady=(self.px(4), self.px(8)))
        footer = tk.Frame(self.bottom, bg=BG)
        footer.pack(fill="x", side="bottom", pady=(self.px(4), 0))
        self.l_hint = tk.Label(self.bottom, bg=BG, fg=DIM, font=self.f_small, anchor="w",
                               text="按 1-3 选择，Esc 关闭")
        self.l_hint.pack(fill="x", side="bottom", before=footer)
        tk.Label(footer, text="进度已记 · 随时可歇", bg=BG, fg=DIM,
                 font=self.f_small).pack(side="left")
        tk.Button(footer, text="暂歇 · Esc", bg=BTN, fg=FG, activebackground=BTN_H,
                  activeforeground=FG, font=self.f_small, relief="flat", bd=0,
                  padx=self.px(10), pady=self.px(4), command=self.hide).pack(side="right")
        self.btns = []
        for i in range(3):
            b = tk.Button(self.bottom, bg=BTN, fg=FG, activebackground=BTN_H, activeforeground=FG,
                          relief="flat", bd=0, font=self.f_btn, anchor="w", justify="left",
                          wraplength=self.width - self.px(52), padx=self.px(10),
                          pady=self.px(5), cursor="hand2", takefocus=False,
                          command=lambda k=i: self.pick(k))
            b.bind("<Enter>", lambda e, w=b: w.configure(bg=BTN_H))
            b.bind("<Leave>", lambda e, w=b: w.configure(bg=BTN))
            self.btns.append(b)

        mid = tk.Frame(r, bg=BG)
        # 中段使用剩余空间，剧情和前情页都可滚动；窗口放大后不再让按钮覆盖正文。
        mid.pack(fill="both", expand=True, side="top", padx=(14, 8), pady=(10, 0))
        sb = tk.Scrollbar(mid, orient="vertical")
        self.txt = tk.Text(mid, bg=BG, fg=FG, font=self.f_body, wrap="char", bd=0, highlightthickness=0,
                           padx=self.px(8), pady=self.px(4), spacing1=self.px(4),
                           spacing2=self.px(4), spacing3=self.px(10), yscrollcommand=sb.set,
                           cursor="arrow", state="disabled", insertwidth=0)
        sb.config(command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("old", foreground=DIM)
        self.txt.tag_configure("new", foreground=FG)
        self.txt.tag_configure("sep", foreground="#4a4f5c", justify="center")
        self.txt.tag_configure("heading", foreground=ACC, font=self.f_head)
        self.txt.tag_configure("choice", foreground=DIM, font=self.f_btn)

        r.bind("<Key>", self._key)
        r.bind("<Escape>", lambda e: self.hide())
        r.bind("<Configure>", self._on_cfg)

    def _place(self):
        r = self.root
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x, y = sw - self.width - self.px(24), sh - self.height - self.px(70)
        g = self.game.s.get("ui", {}).get("pos")
        if g and 0 <= g[0] <= sw - self.width and 0 <= g[1] <= sh - self.height:
            x, y = g
        r.geometry("%dx%d+%d+%d" % (self.width, self.height, max(0, x), max(0, y)))
        r.update()
        u = ctypes.windll.user32
        u.GetParent.argtypes = [ctypes.c_void_p]
        u.GetParent.restype = ctypes.c_void_p
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self._hwnd = u.GetParent(r.winfo_id()) or r.winfo_id()
        ctypes.windll.user32.ShowWindow(self._hwnd, SW_HIDE)
        r.attributes("-alpha", 1.0)

    def _render(self):
        g = self.game
        h = g.header()
        self.l_title.config(text=h[0])
        self.l_realm.config(text=h[1])
        self.l_prog.config(text=h[2])
        self.l_stat.config(text=h[3])
        t = self.txt
        t.config(state="normal")
        t.delete("1.0", "end")
        sc, last = g.s['scene'], g.s.get('last_turn')
        if self.page == "history":
            self.l_chapter.config(text="前情 · 最近几幕")
            t.insert("end", g.story_text(), "new")
            labels = ["返回当前一幕"]
        elif self.page == "result" and last:
            self.l_chapter.config(text=last['title'] + " · 此回所得")
            t.insert("end", "你选择了：" + last['choice'] + "\n\n", "choice")
            t.insert("end", last['result'] + "\n", "new")
            if last['finished']:
                t.insert("end", "\n此番历练告一段落。且收心暂歇，仙途来日再续。", "heading")
            labels = ["此篇已了 · 另启一段" if last['finished'] else "继续这一段历练"]
        else:
            self.l_chapter.config(text="%s · %d/%d" %
                                  (sc.get('title', '叩问仙途'), sc.get('step', 0) + 1, sc.get('total', 1)))
            if self.prefix and g.s['turn'] == 0:
                t.insert("end", g.s['log'][0] + "\n\n", "new")
            elif self.prefix:
                t.insert("end", self.prefix + "\n\n", "choice")
            # 结算后直接接上下一幕，保留上一回合的选择和结果，
            # 让三回合短篇在同一页自然连起来，不必额外点击“结果”页。
            if last and g.s['turn'] > 0:
                t.insert("end", "上一回合 · " + last['choice'] + "\n", "choice")
                t.insert("end", last['result'] + "\n", "old")
                if last.get('finished'):
                    t.insert("end", "\n这一段历练已收束，新的因缘正在前方。\n", "heading")
                t.insert("end", "\n· · ·\n\n", "sep")
            t.insert("end", sc['text'], "new")
            labels = g.options()
        t.config(state="disabled")
        # 连续显示上一回合和当前一幕时，默认落在最新文字，避免新剧情被旧结果顶出视野。
        t.see("end")
        for i, b in enumerate(self.btns):
            b.pack_forget()
            if i < len(labels):
                b.config(text="%d. %s" % (i + 1, labels[i]))
                b.pack(fill="x", pady=2, before=self.l_hint)
        hint = ("按 1 继续 · Esc 暂歇" if len(labels) == 1
                else "按 1–3 选择 · 结果随后接入下一幕")
        if self.state == "rest":
            hint = "回复已完成 · " + hint
        self.l_hint.config(text=hint)
        self._title()

    def _title(self):
        t = {"thinking": "修仙 · 回复中…", "rest": "修仙 · 回复已完成", "idle": "修仙"}[self.state]
        self.root.title(t)

    # ------------------------------------------------------------ 显示/隐藏
    def show(self, activate=False):
        u = ctypes.windll.user32
        u.ShowWindow(self._hwnd, 5 if activate else SW_SHOWNOACTIVATE)
        u.SetWindowPos(self._hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if activate:
            self.root.focus_force()
        self.visible = True
        self._shown_at = time.monotonic()
        self._auto_shown = not activate

    def hide(self):
        # 仅在自动弹出后很快主动关闭时设置冷却。回复结束后的“暂歇”不
        # 会改变下次自动提醒；冷却写入存档，重启程序仍然有效。
        if (self.state == "thinking" and self._auto_shown and self.visible and self._shown_at is not None
                and time.monotonic() - self._shown_at <= DISMISS_QUICK_SECONDS):
            self.suppress_until = time.time() + DISMISS_COOLDOWN_SECONDS
            self.game.s.setdefault("ui", {})["suppress_until"] = self.suppress_until
            self.game.save()
        ctypes.windll.user32.ShowWindow(self._hwnd, SW_HIDE)
        self.visible = False
        self._shown_at = None
        self._auto_shown = False
        self._save_pos()

    def _on_cfg(self, e):
        if e.widget is not self.root:
            return
        if self._cfg_job:
            self.root.after_cancel(self._cfg_job)
        self._cfg_job = self.root.after(600, self._save_pos)

    def _save_pos(self):
        self._cfg_job = None
        try:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            if (x, y) != tuple(self.game.s.setdefault("ui", {}).get("pos", ())):
                self.game.s["ui"]["pos"] = [x, y]
                self.game.save()
        except Exception:
            pass

    # ------------------------------------------------------------ 交互
    def _key(self, e):
        if e.char and e.char in "123":
            self.pick(int(e.char) - 1)

    def pick(self, i):
        if self.page == "history":
            if i == 0:
                self.page = self._before_history
                self._render()
            return
        if self.page == "result":
            if i == 0:
                self.page = "scene"
                self._render()
            return
        if i < 0 or i >= len(self.game.options()):
            return
        self.prefix = ""
        self.game.choose(i)
        self.session_rounds += 1
        # 结果与下一幕连续显示，减少一次点击，也避免剧情被截断。
        self.page = "scene"
        self._render()

    def history(self):
        if self.page != "history":
            self._before_history = self.page
            self.page = "history"
        else:
            self.page = self._before_history
        self._render()

    # ------------------------------------------------------------ 检测
    def _watch(self):
        mon = detector.Monitor()
        while not self.stop.is_set():
            try:
                if not self.paused:
                    ev = mon.poll(enabled=self.enabled)
                    if ev:
                        self.q.put(ev)
                else:
                    mon.active = False
                    mon._since_busy = None
            except Exception:
                pass
            self.stop.wait(0.8)

    def _on_start(self):
        self.state = "thinking"
        self.session_rounds = 0
        if self.page == "result":
            self.page = "scene"
            self._render()
        self._title()
        # 冷却只屏蔽自动弹出，不影响托盘“打开游戏窗口”。多个工具交错
        # 回复时 Monitor 只发出一次 start，这里也不会重复重置冷却。
        if self.visible:
            # 托盘手动打开的窗口沿用原来的显示来源，避免再次调用
            # show(False) 把它误记成自动弹出。
            return
        if not self.visible and not auto_popup_allowed(self.suppress_until):
            return
        self.show(activate=False)

    def _on_end(self):
        if self.state == "thinking":
            self.state = "rest"
            self._title()

    def _pump(self):
        try:
            while True:
                c = self.q.get_nowait()
                if c == "start":
                    self._on_start()
                elif c == "end":
                    self._on_end()
                elif c == "show":
                    self.show(activate=True)
                elif c == "pause":
                    self.paused = not self.paused
                elif c == "autostart":
                    tr.set_autostart(not tr.autostart_enabled(), os.path.join(HERE, "xiuxian.py"))
                elif c.startswith("tool:"):
                    name = c.split(":", 1)[1]
                    selected = set(self.enabled)
                    selected.symmetric_difference_update([name])
                    self.enabled = frozenset(selected)
                    self.game.s.setdefault("ui", {})["enabled_tools"] = sorted(selected)
                    self.game.save()
                elif c == "reset":
                    self.show(activate=True)
                    if messagebox.askyesno("重置存档", "确定要重新开始吗？当前进度会被清空。", parent=self.root):
                        self.game.reset()
                        self.prefix = ""
                        self.page = "scene"
                        self._render()
                elif c == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(150, self._pump)

    def _menu(self):
        return [
            ("show", "打开游戏窗口", False),
            ("pause", "暂停监测", self.paused),
            None,
        ] + [("tool:" + n, "监测 " + n, n in self.enabled) for n in detector.ADAPTERS] + [
            None,
            ("autostart", "开机自启", tr.autostart_enabled()),
            None,
            ("reset", "重置存档…", False),
            ("quit", "退出", False),
        ]

    def quit(self):
        self.stop.set()
        self._save_pos()
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--data", default=HERE)
    a = ap.parse_args()
    ok, _h = single_instance(a.demo)
    if not ok:
        return
    App(a.data, demo=a.demo).run()


if __name__ == "__main__":
    main()
