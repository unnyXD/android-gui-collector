import argparse
import json
import sys
import time
from pathlib import Path

from .adb import Adb, AdbError
from .config import Config

KEY_BACK = "4"
KEY_HOME = "3"
KEY_ENTER = "66"


class Replayer:
    def __init__(self, trajectory_path: Path, adb: Adb, delay: float = 1.0, shot_dir: Path | None = None, interactive: bool = False):
        self.data = json.loads(Path(trajectory_path).read_text(encoding="utf-8"))
        self.adb = adb
        self.delay = delay
        self.shot_dir = shot_dir
        self.interactive = interactive
        self.w, self.h = adb.screen_size()

    # ---------- 坐标换算（归一化 0-1 -> 像素） ----------
    def _to_pixel(self, norm) -> tuple[int, int]:
        return int(norm[0] * self.w), int(norm[1] * self.h)

    def _point(self, params) -> tuple[int, int] | None:
        """coordinate 优先，否则取 bbox 中心。"""
        if params.get("coordinate"):
            return self._to_pixel(params["coordinate"])
        bbox = params.get("bbox")
        if bbox:
            cx = (bbox[0] + bbox[2]) / 2
            cy = (bbox[1] + bbox[3]) / 2
            return self._to_pixel((cx, cy))
        return None

    # ---------- 动作执行 ----------
    def _execute(self, step: dict) -> bool:
        action = step["action"]["action"]
        params = step["action"]["action_params"]
        try:
            if action in ("click", "open"):
                pt = self._point(params)
                if pt is None:
                    print(f"    [跳过] {action} 无坐标/bbox")
                    return False
                self.adb.touch(*pt)
                print(f"    tap {pt}")
            elif action == "swipe":
                start = params.get("start")
                end = params.get("end")
                if not start or not end:
                    print("    [跳过] swipe 无起止坐标")
                    return False
                (x1, y1), (x2, y2) = self._to_pixel(start), self._to_pixel(end)
                self.adb.swipe(x1, y1, x2, y2, 300)
                print(f"    swipe {x1},{y1} -> {x2},{y2}")
            elif action == "input":
                text = params.get("text", "")
                if not text.isascii():
                    print(f"    [警告] input text 不支持非 ASCII（含中文），已跳过文本输入: {text}")
                else:
                    pt = self._point(params)
                    if pt:
                        self.adb.touch(*pt)
                        time.sleep(0.5)
                    self.adb.text(text)
                    print(f"    input '{text}' @ {pt}")
            elif action == "back":
                self.adb.key(KEY_BACK)
                print("    key BACK")
            elif action == "home":
                self.adb.key(KEY_HOME)
                print("    key HOME")
            elif action == "terminate":
                print(f"    terminate ({params.get('status', 'success')})")
            else:
                print(f"    [跳过] 未知动作 {action}")
            return True
        except AdbError as e:
            print(f"    [失败] {e}")
            return False

    # ---------- 主流程 ----------
    def replay(self) -> bool:
        print(f"轨迹: {self.data.get('execution_id', '')}")
        print(f"任务: {self.data.get('goal', '')}")
        print(f"包名: {self.data.get('app_package_names', '')} | 共 {len(self.data['messages'])} 步\n")

        all_ok = True
        for step in self.data["messages"]:
            no = step["step"]
            act = step["action"]["action"]
            thought = step.get("thought", "")
            print(f"\n[{no}] {act} {f'- {thought}' if thought else ''}")
            ok = self._execute(step)
            all_ok = all_ok and ok

            if self.shot_dir and act != "terminate":
                shot = self.shot_dir / f"replay_step_{no:02d}.png"
                self.adb.screencap(shot)
                print(f"    回放后截图: {shot}")
                print(f"    预期截图: {step.get('oss_url', '')}")

            if self.interactive and act != "terminate":
                try:
                    r = input("    回车继续，输入 q 停止: ").strip().lower()
                except EOFError:
                    r = ""
                if r == "q":
                    print("已停止。")
                    return all_ok

            time.sleep(self.delay)

        print("\n回放完成，全部动作:", "成功" if all_ok else "有跳过/失败")
        return all_ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="回放 Android GUI 轨迹 (trajectory.json)")
    parser.add_argument("trajectory", help="trajectory.json 路径")
    parser.add_argument("--device", default="", help="设备序列号，默认自动选择")
    parser.add_argument("--delay", type=float, default=1.0, help="每步间隔秒数（默认 1.0）")
    parser.add_argument("--shot-dir", default="", help="回放后截图保存目录（默认不截图）")
    parser.add_argument("--interactive", action="store_true", help="每步执行后等待确认再继续")
    parser.add_argument("--adb", default="", help="adb 路径")
    args = parser.parse_args()

    cfg = Config().resolve()
    adb = Adb(args.adb or cfg.adb_path, args.device)
    try:
        devices = adb.devices()
    except AdbError as e:
        print(f"[错误] {e}")
        return 1
    if not devices:
        print("没有检测到设备，请连接手机并开启 USB 调试。")
        return 1
    if len(devices) > 1 and not args.device:
        print("检测到多台设备，请用 --device 指定：", ", ".join(devices))
        return 1
    if not args.device:
        args.device = devices[0]
    print(f"设备: {args.device}")

    shot_dir = Path(args.shot_dir) if args.shot_dir else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)

    rp = Replayer(Path(args.trajectory), adb, args.delay, shot_dir, args.interactive)
    ok = rp.replay()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())