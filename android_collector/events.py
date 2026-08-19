import re
import subprocess
import threading
import time

from .adb import AdbError


class TouchEventMonitor:
    """后台监听 adb shell getevent，自动识别触摸设备并按屏幕分辨率缩放坐标。"""

    _X = re.compile(r"ABS_MT_POSITION_X\s+([0-9a-fA-F]+)")
    _Y = re.compile(r"ABS_MT_POSITION_Y\s+([0-9a-fA-F]+)")
    _TID = re.compile(r"ABS_MT_TRACKING_ID\s+([0-9a-fA-F]+)")

    def __init__(self, adb, screen_size=(0, 0)):
        self.adb = adb
        self.screen_w, self.screen_h = screen_size
        self._device: str | None = None
        self._max_x: int | None = None
        self._max_y: int | None = None
        self._buffer: list[tuple[float, int, int, bool]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._enabled = False
        self._error: str | None = None

    # ---------- 设备探测 ----------
    def _probe_device(self):
        """解析 getevent -p，挑出触摸设备（INPUT_PROP_DIRECT 或宽高比最接近屏幕）。
        getevent -p 中 ABS 码 0035=POSITION_X, 0036=POSITION_Y。"""
        try:
            out = self.adb.run(["shell", "getevent", "-p"], timeout=30)
        except AdbError as e:
            self._error = f"probe 失败: {e}"
            return
        best = None
        best_diff = None
        for block in out.split("add device"):
            mx = re.search(r"0035\s*: value \d+, min \d+, max (\d+)", block)
            my = re.search(r"0036\s*: value \d+, min \d+, max (\d+)", block)
            if not mx or not my:
                continue
            max_x, max_y = int(mx.group(1)), int(my.group(1))
            m = re.search(r"/dev/input/(\S+)", block)
            if not m or max_x <= 0 or max_y <= 0:
                continue
            path = "/dev/input/" + m.group(1)
            direct = "INPUT_PROP_DIRECT" in block
            if direct:
                self._device, self._max_x, self._max_y = path, max_x, max_y
                return
            if self.screen_w and self.screen_h:
                diff = abs(max_x / max_y - self.screen_w / self.screen_h)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best = (path, max_x, max_y)
        if best:
            self._device, self._max_x, self._max_y = best

    # ---------- 生命周期 ----------
    def start(self) -> bool:
        self._probe_device()
        if self._device:
            args = ["shell", "getevent", "-l", self._device]
        else:
            args = ["shell", "getevent", "-l"]
            self._error = "未识别到触摸设备，使用全设备监听"
        try:
            self._proc = self.adb._popen(args)
        except Exception as e:
            self._error = str(e)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(self._proc,), daemon=True)
        self._thread.start()
        self._enabled = True
        return True

    def _run(self, proc: subprocess.Popen):
        cur_x = cur_y = None
        tid_active = False
        while not self._stop.is_set():
            line = proc.stdout.readline()
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="replace")
            except Exception:
                continue
            m = self._X.search(text)
            if m:
                cur_x = int(m.group(1), 16)
            m = self._Y.search(text)
            if m:
                cur_y = int(m.group(1), 16)
            m = self._TID.search(text)
            if m:
                tid = int(m.group(1), 16)
                tid_active = tid != 0xFFFFFFFF and tid != -1
            if "SYN_REPORT" in text and cur_x is not None and cur_y is not None:
                with self._lock:
                    self._buffer.append((time.time(), cur_x, cur_y, tid_active))

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    # ---------- 坐标换算 ----------
    def _to_pixel(self, x: int, y: int) -> tuple[int, int]:
        if self._max_x and self._max_y and self.screen_w and self.screen_h:
            return (
                round(x * self.screen_w / self._max_x),
                round(y * self.screen_h / self._max_y),
            )
        return x, y

    # ---------- 消费 ----------
    def _drain(self) -> list[tuple[float, int, int, bool]]:
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        return [(t, *self._to_pixel(x, y), down) for t, x, y, down in items]

    @staticmethod
    def analyze(items: list[tuple[float, int, int, bool]]):
        """将一段时间内的触点聚合成 (kind, data)。
        kind: 'tap' | 'swipe' | None; data: 坐标或起止坐标。"""
        if not items:
            return None, None
        pts = [(x, y) for _, x, y, _ in items]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span = max(1, max(xs) - min(xs) + max(ys) - min(ys))
        if span > 60:
            return "swipe", ((pts[0][0], pts[0][1]), (pts[-1][0], pts[-1][1]))
        if len(pts) <= 15:
            return "tap", (pts[0][0], pts[0][1])
        return "swipe", ((pts[0][0], pts[0][1]), (pts[-1][0], pts[-1][1]))

    def capture_since_last(self):
        return self._drain()

    def poll_completed_gesture(self, min_idle: float = 0.8):
        """若缓冲区内有触摸数据且已静默超过 min_idle 秒，视为一次完整手势，
        取出并返回 (kind, data)；否则返回 None。用于自动记录模式。"""
        with self._lock:
            if not self._buffer:
                return None
            if time.time() - self._buffer[-1][0] < min_idle:
                return None
            items = list(self._buffer)
            self._buffer.clear()
        scaled = [(t, *self._to_pixel(x, y), down) for t, x, y, down in items]
        kind, data = self.analyze(scaled)
        return kind, data