"""
桌面番茄钟应用 — 基于 Python tkinter 实现。

工作流程：
  工作 (25分钟) → 短休息 (5分钟) → 工作 → 短休息 → ... → 第4个番茄后 → 长休息 (15分钟) → 循环

核心机制：
  - 使用 root.after(1000, callback) 实现每秒倒计时，而非 time.sleep()，避免阻塞 GUI 主循环
  - 状态机模式：current_phase 在 PHASE_WORK / PHASE_SHORT_BREAK / PHASE_LONG_BREAK 之间切换
  - 倒计时归零时自动触发 _phase_complete()，播放提示音并切换到下一阶段
  - 用户可在设置面板中自定义各阶段时长
"""

import tkinter as tk
import winsound
import threading
import time

# ── 默认时长常量（单位：分钟） ──
WORK_MIN = 25
SHORT_BREAK_MIN = 5
LONG_BREAK_MIN = 15
POMODOROS_BEFORE_LONG_BREAK = 4

# ── 阶段常量 — 避免散落字符串字面量 ──
PHASE_WORK = "work"
PHASE_SHORT_BREAK = "short_break"
PHASE_LONG_BREAK = "long_break"

# ── 配色方案 — 暖调蜜桃/珊瑚主题 ──
COLORS = {
    PHASE_WORK: "#E79796",
    PHASE_SHORT_BREAK: "#B8B38E",
    PHASE_LONG_BREAK: "#FFB284",
    "bg": "#FFF5F0",
    "card": "#FDF0EB",
    "text": "#4A3330",
    "subtext": "#9B8280",
    "accent": "#E79796",
    "button_bg": "#F0D5CE",
    "button_hover": "#E8C4BA",
    "progress_bg": "#EDE0DA",
    "start_disabled": "#E8C4BA",
    "pause_disabled": "#F5E1DB",
}

PHASE_LABELS = {
    PHASE_WORK: "FOCUS",
    PHASE_SHORT_BREAK: "SHORT BREAK",
    PHASE_LONG_BREAK: "LONG BREAK",
}


class PomodoroTimer:
    """番茄钟主控制器。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Pomodoro Timer")
        self.root.geometry("420x560")
        self.root.configure(bg=COLORS["bg"])
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # ── 状态初始化 ──
        self.timer_running = False
        self.remaining_seconds = WORK_MIN * 60
        self.current_phase = PHASE_WORK
        self.completed_pomodoros = 0
        self.after_id = None
        self.always_on_top = tk.BooleanVar(value=False)
        self._sound_playing = False
        self._last_rendered_phase = None
        self._last_rendered_sessions = None
        self._phase_total_seconds = WORK_MIN * 60

        self._build_ui()
        self._update_display()

    # ──────────────────────────────────────────────────────────────────
    # UI 构建
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.title_label = tk.Label(
            self.root, text="POMODORO",
            font=("Segoe UI", 12, "bold"),
            bg=COLORS["bg"], fg=COLORS["subtext"],
        )
        self.title_label.pack(pady=(30, 5))

        self.phase_label = tk.Label(
            self.root, text="FOCUS",
            font=("Segoe UI", 10, "bold"),
            bg=COLORS["bg"], fg=COLORS[PHASE_WORK],
        )
        self.phase_label.pack()

        self.canvas_size = 260
        self.canvas = tk.Canvas(
            self.root, width=self.canvas_size, height=self.canvas_size,
            bg=COLORS["bg"], highlightthickness=0,
        )
        self.canvas.pack(pady=(15, 5))
        self._draw_timer_face()

        self.timer_text = self.canvas.create_text(
            self.canvas_size // 2, self.canvas_size // 2,
            text="25:00", font=("Segoe UI", 42, "bold"), fill=COLORS["text"],
        )

        self.session_label = tk.Label(
            self.root, text="Sessions: 0",
            font=("Segoe UI", 10), bg=COLORS["bg"], fg=COLORS["subtext"],
        )
        self.session_label.pack(pady=(10, 15))

        # ── 控制按钮 ──
        btn_frame = tk.Frame(self.root, bg=COLORS["bg"])
        btn_frame.pack()

        self.start_btn = tk.Button(
            btn_frame, text="START", command=self.start,
            font=("Segoe UI", 11, "bold"),
            bg=COLORS["accent"], fg=COLORS["text"],
            activebackground="#D48786", activeforeground=COLORS["text"],
            relief=tk.FLAT, padx=24, pady=8, cursor="hand2",
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.pause_btn = tk.Button(
            btn_frame, text="PAUSE", command=self.pause,
            font=("Segoe UI", 11, "bold"),
            bg=COLORS["button_bg"], fg=COLORS["text"],
            activebackground=COLORS["button_hover"], activeforeground=COLORS["text"],
            relief=tk.FLAT, padx=24, pady=8, cursor="hand2",
            state=tk.DISABLED,
        )
        self.pause_btn.pack(side=tk.LEFT, padx=5)

        self.reset_btn = tk.Button(
            btn_frame, text="RESET", command=self.reset,
            font=("Segoe UI", 11, "bold"),
            bg=COLORS["button_bg"], fg=COLORS["text"],
            activebackground=COLORS["button_hover"], activeforeground=COLORS["text"],
            relief=tk.FLAT, padx=24, pady=8, cursor="hand2",
        )
        self.reset_btn.pack(side=tk.LEFT, padx=5)

        self.skip_btn = tk.Button(
            self.root, text="Skip →", command=self.skip,
            font=("Segoe UI", 9),
            bg=COLORS["bg"], fg=COLORS["subtext"],
            activebackground=COLORS["bg"], activeforeground=COLORS["text"],
            relief=tk.FLAT, padx=12, pady=4, cursor="hand2", borderwidth=0,
        )
        self.skip_btn.pack(pady=(5, 10))

        # ── 设置面板 ──
        settings_frame = tk.Frame(self.root, bg=COLORS["card"])
        settings_frame.pack(fill=tk.X, padx=30, pady=(5, 0))

        dur_frame = tk.Frame(settings_frame, bg=COLORS["card"])
        dur_frame.pack(pady=(15, 5))

        self._create_duration_row(dur_frame, "Work", WORK_MIN, 0, "work_var")
        self._create_duration_row(dur_frame, "Break", SHORT_BREAK_MIN, 1, "break_var")
        self._create_duration_row(dur_frame, "Long", LONG_BREAK_MIN, 2, "long_var")

        top_frame = tk.Frame(settings_frame, bg=COLORS["card"])
        top_frame.pack(pady=(5, 15))
        self.top_check = tk.Checkbutton(
            top_frame, text="Always on top",
            variable=self.always_on_top, command=self._toggle_always_on_top,
            bg=COLORS["card"], fg=COLORS["subtext"],
            selectcolor=COLORS["button_bg"],
            activebackground=COLORS["card"], activeforeground=COLORS["text"],
            font=("Segoe UI", 9),
        )
        self.top_check.pack()

    def _create_duration_row(self, parent, label, default_val, row, attr_name):
        """创建一行时长设置控件（标签 + Spinbox + "min" 单位）。"""
        tk.Label(
            parent, text=label,
            font=("Segoe UI", 10), bg=COLORS["card"], fg=COLORS["text"],
            width=6, anchor="w",
        ).grid(row=row, column=0, padx=(0, 10), pady=3)

        var = tk.IntVar(value=default_val)
        setattr(self, attr_name, var)
        spin = tk.Spinbox(
            parent, from_=1, to=120, textvariable=var, width=5,
            font=("Segoe UI", 10),
            bg=COLORS["button_bg"], fg=COLORS["text"],
            buttonbackground=COLORS["button_bg"],
            readonlybackground=COLORS["button_bg"],
            relief=tk.FLAT, justify="center",
        )
        spin.grid(row=row, column=1, pady=3)

        tk.Label(
            parent, text="min",
            font=("Segoe UI", 9), bg=COLORS["card"], fg=COLORS["subtext"],
        ).grid(row=row, column=2, padx=(5, 0), pady=3)

    def _draw_timer_face(self):
        """绘制底环和初始进度弧。进度弧从 12 点方向顺时针，0~-359.99° 对应 100%~0%。"""
        cx = self.canvas_size // 2
        cy = self.canvas_size // 2
        self.radius = 110
        self.circle_width = 8

        self.canvas.create_oval(
            cx - self.radius, cy - self.radius,
            cx + self.radius, cy + self.radius,
            outline=COLORS["progress_bg"], width=self.circle_width,
        )

        self.progress_arc = self.canvas.create_arc(
            cx - self.radius, cy - self.radius,
            cx + self.radius, cy + self.radius,
            start=90, extent=0,
            outline=COLORS[PHASE_WORK], width=self.circle_width, style="arc",
        )

    # ──────────────────────────────────────────────────────────────────
    # 显示更新
    # ──────────────────────────────────────────────────────────────────

    def _update_display(self):
        """刷新倒计时数字、进度弧、阶段标签、番茄计数。"""
        mins = self.remaining_seconds // 60
        secs = self.remaining_seconds % 60
        self.canvas.itemconfig(self.timer_text, text=f"{mins:02d}:{secs:02d}")

        fraction = self.remaining_seconds / self._phase_total_seconds if self._phase_total_seconds > 0 else 0
        extent = -fraction * 359.99

        color = COLORS[self.current_phase]
        self.canvas.itemconfig(self.progress_arc, extent=extent, outline=color)

        if self.current_phase != self._last_rendered_phase:
            self.phase_label.config(text=PHASE_LABELS[self.current_phase], fg=color)
            self._last_rendered_phase = self.current_phase

        if self.completed_pomodoros != self._last_rendered_sessions:
            self.session_label.config(text=f"Sessions: {self.completed_pomodoros}")
            self._last_rendered_sessions = self.completed_pomodoros

    # ──────────────────────────────────────────────────────────────────
    # 控制逻辑
    # ──────────────────────────────────────────────────────────────────

    def start(self):
        self.timer_running = True
        self._set_buttons_running()
        self._tick()

    def pause(self):
        self.timer_running = False
        self._cancel_after()
        self._set_buttons_stopped()

    def reset(self):
        self.timer_running = False
        self._cancel_after()
        self.current_phase = PHASE_WORK
        self._phase_total_seconds = self.work_var.get() * 60
        self.remaining_seconds = self._phase_total_seconds
        self._update_display()
        self._set_buttons_stopped()

    def skip(self):
        self._cancel_after()
        self._phase_complete()

    # ──────────────────────────────────────────────────────────────────
    # 计时核心
    # ──────────────────────────────────────────────────────────────────

    def _tick(self):
        if not self.timer_running:
            return

        if self.remaining_seconds > 0:
            self.remaining_seconds -= 1
            self._update_display()
            self.after_id = self.root.after(1000, self._tick)
        else:
            self._phase_complete()

    def _phase_complete(self):
        self.timer_running = False
        self._play_sound_async()

        # 状态机：阶段切换
        if self.current_phase == PHASE_WORK:
            self.completed_pomodoros += 1
            if self.completed_pomodoros % POMODOROS_BEFORE_LONG_BREAK == 0:
                self.current_phase = PHASE_LONG_BREAK
                self._phase_total_seconds = self.long_var.get() * 60
            else:
                self.current_phase = PHASE_SHORT_BREAK
                self._phase_total_seconds = self.break_var.get() * 60
        else:
            self.current_phase = PHASE_WORK
            self._phase_total_seconds = self.work_var.get() * 60

        self.remaining_seconds = self._phase_total_seconds
        self._update_display()
        self._set_buttons_stopped()
        self._flash_window()

    # ──────────────────────────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────────────────────────

    def _cancel_after(self):
        """取消 root.after 定时器，消除 4 处重复代码。"""
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def _set_buttons_running(self):
        """计时运行中的按钮状态。"""
        self.start_btn.config(state=tk.DISABLED, bg=COLORS["start_disabled"])
        self.pause_btn.config(state=tk.NORMAL, bg=COLORS["button_bg"])

    def _set_buttons_stopped(self):
        """计时停止时的按钮状态（复位到"可开始"）。"""
        self.start_btn.config(state=tk.NORMAL, bg=COLORS["accent"])
        self.pause_btn.config(state=tk.DISABLED, bg=COLORS["button_bg"])

    def _play_sound_async(self):
        """后台线程播放提示音，防止快速连续跳过产生重叠声音。"""
        if self._sound_playing:
            return
        self._sound_playing = True

        def _wrapper():
            self._play_sound()
            self._sound_playing = False

        threading.Thread(target=_wrapper, daemon=True).start()

    def _play_sound(self):
        for _ in range(3):
            winsound.Beep(880, 200)
            time.sleep(0.1)
        winsound.Beep(1100, 500)

    def _flash_window(self):
        """短暂置顶窗口以吸引注意。使用 root.after 替代 time.sleep，避免阻塞主线程。"""
        try:
            self.root.attributes("-topmost", True)
            if not self.always_on_top.get():
                self.root.after(100, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def _toggle_always_on_top(self):
        self.root.attributes("-topmost", self.always_on_top.get())

    def on_close(self):
        self.timer_running = False
        self._cancel_after()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PomodoroTimer(root)
    root.mainloop()
