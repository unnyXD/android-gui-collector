import subprocess
from pathlib import Path


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, adb_path: str = "adb", device: str = ""):
        self.adb_path = adb_path
        self.device = device

    def _cmd(self, args: list[str]) -> list[str]:
        cmd = [self.adb_path]
        if self.device:
            cmd += ["-s", self.device]
        return cmd + args

    def run(self, args: list[str], timeout: int = 60) -> str:
        try:
            p = subprocess.run(
                self._cmd(args), capture_output=True, timeout=timeout
            )
        except FileNotFoundError:
            raise AdbError(f"找不到 adb：{self.adb_path}，请安装 platform-tools 或设置 ADB_PATH")
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb 命令超时: {' '.join(args)}")
        out = p.stdout.decode("utf-8", errors="replace")
        err = p.stderr.decode("utf-8", errors="replace")
        if p.returncode != 0:
            raise AdbError(f"adb 失败({p.returncode}): {' '.join(args)}\n{err.strip()}")
        return out

    def devices(self) -> list[str]:
        out = self.run(["devices"])
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        result = []
        for l in lines[1:]:
            if l.endswith("\tdevice"):
                result.append(l.split("\t")[0])
        return result

    def current_app(self) -> str:
        out = self.run(["shell", "dumpsys", "window"])
        for line in out.splitlines():
            line = line.strip()
            if "mCurrentFocus" in line and "Window{" in line:
                import re

                m = re.search(r"([\w.]+)/", line)
                if m:
                    return m.group(1)
        return ""

    def screen_size(self) -> tuple[int, int]:
        out = self.run(["shell", "wm", "size"])
        import re

        m = re.search(r"(\d+)x(\d+)", out)
        if not m:
            raise AdbError(f"无法解析屏幕尺寸: {out}")
        return int(m.group(1)), int(m.group(2))

    def screencap(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.run(
            self._cmd(["exec-out", "screencap", "-p"]),
            capture_output=True,
            timeout=30,
        ).stdout
        # 某些 Windows 版 adb 会在 PNG 末尾多追加 \r\n，只截断到 IEND 块结束，
        # 绝不能全局替换 \r\n（会破坏 PNG 签名与压缩数据）
        idx = data.find(b"IEND")
        if idx != -1:
            data = data[: idx + 8]
        path.write_bytes(data)

    def dump_tree(self) -> str:
        """返回 uiautomator dump 的 XML 字符串；失败抛 AdbError。"""
        self.run(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"], timeout=90)
        out = self.run(["shell", "cat", "/sdcard/window_dump.xml"], timeout=30)
        return out

    def touch(self, x: int, y: int) -> None:
        self.run(["shell", "input", "tap", str(int(x)), str(int(y))], timeout=30)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.run(
            ["shell", "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(duration_ms)],
            timeout=30,
        )

    def text(self, s: str) -> None:
        self.run(["shell", "input", "text", s], timeout=30)

    def key(self, keycode: str) -> None:
        self.run(["shell", "input", "keyevent", keycode], timeout=30)

    def _popen(self, args: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            self._cmd(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )