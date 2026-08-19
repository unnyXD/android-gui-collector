import queue
import sys
import threading
import time
from pathlib import Path

from .adb import Adb, AdbError
from .config import Config
from .events import TouchEventMonitor
from .recorder import Recorder

ACTION_SHORTCUTS = {
    "c": "click", "click": "click",
    "o": "open", "open": "open",
    "s": "swipe", "swipe": "swipe",
    "i": "input", "input": "input",
    "b": "back", "back": "back",
    "h": "home", "home": "home",
    "t": "terminate", "terminate": "terminate", "end": "terminate", "done": "terminate",
}


def ask(prompt: str, default: str = "") -> str:
    if default:
        prompt = f"{prompt} [{default}]"
    v = input(prompt + ": ").strip()
    return v if v else default


def parse_annotation(line):
    line = line.strip()
    if ":" in line:
        head, _, rest = line.partition(":")
        head = head.lower()
        if head in ACTION_SHORTCUTS:
            act = ACTION_SHORTCUTS[head]
            if act in ("input", "open"):
                return act, rest.strip(), ""
            return act, "", rest.strip()
        return None, "", line
    if line.lower() in ACTION_SHORTCUTS:
        return ACTION_SHORTCUTS[line.lower()], "", ""
    return None, "", line


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = Config().resolve()

    adb = Adb(cfg.adb_path, cfg.device)
    try:
        devices = adb.devices()
    except AdbError as e:
        print(f"[错误] {e}")
        return 1
    if not devices:
        print("没有检测到已连接的设备，请先用 USB 连接手机并开启 USB 调试。")
        return 1
    if len(devices) > 1 and not cfg.device:
        print("检测到多台设备：")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d}")
        idx = int(input("选择设备序号: "))
        cfg.device = devices[idx]
    elif not cfg.device:
        cfg.device = devices[0]
    print(f"使用设备: {cfg.device}")

    goal = ask("任务指令 (goal/task_desc)", "查看当前应用并完成一个操作")
    app_names = ask("应用名 (逗号分隔)", "")
    tag = ask("批次 tag", cfg.tag_prefix + "-batch1")

    rec = Recorder(cfg)
    try:
        rec.start_session(goal, app_names, tag)
    except AdbError as e:
        print(f"[错误] {e}")
        return 1

    monitor = TouchEventMonitor(rec.adb, (rec.screen_w, rec.screen_h))
    if not monitor.start():
        print("[警告] getevent 监听失败，坐标/bbox 将无法自动获取:", monitor._error)

    print("\n==== 采集模式 ====")
    print("手机操作 -> 自动截图记录动作，随后在终端补写该步骤的 thought")
    print("结束: 输入 end  退出: q  想给下一步预写备注可先输入 c:点击会员 等")

    start_shot, start_dump = rec.capture_state()
    rec.pending_shot = start_shot
    rec.pending_dump = start_dump
    print(f"[i] 起始状态已截图: {start_shot}")

    def parse_annotation(line):
        line = line.strip()
        if ":" in line:
            head, _, rest = line.partition(":")
            head = head.lower()
            if head in ACTION_SHORTCUTS:
                act = ACTION_SHORTCUTS[head]
                if act in ("input", "open"):
                    return act, rest.strip(), ""
                return act, "", rest.strip()
            return None, "", line
        if line.lower() in ACTION_SHORTCUTS:
            return ACTION_SHORTCUTS[line.lower()], "", ""
        return None, "", line

    def infer_action(kind, data, annotation):
        act, text, thought = annotation or (None, "", "")
        if act is None:
            pkg = rec._packages_seen
            prev = pkg[-2] if len(pkg) >= 2 else None
            cur = pkg[-1] if pkg else ""
            if prev and any(h in prev.lower() for h in Recorder.LAUNCHER_HINTS) and cur and not any(h in cur.lower() for h in Recorder.LAUNCHER_HINTS):
                act, text = "open", rec.app_names
            elif kind == "swipe":
                act = "swipe"
            else:
                act = "click"
        return act, text, thought

    cmd_queue: queue.Queue = queue.Queue()
    _finish = {"end": False, "quit": False}

    def reader(q):
        for line in sys.stdin:
            q.put(line)

    threading.Thread(target=reader, args=(cmd_queue,), daemon=True).start()

    def read_line(prompt):
        """从终端读一行（经队列，避免与 reader 线程争抢 stdin）。
        返回 (文本, is_control)。控制命令 end/q 会被识别。"""
        print(prompt, end="", flush=True)
        while True:
            line = cmd_queue.get().strip()
            norm = line.lower().replace("ｑ", "q")
            if norm in ("end", "done", "t", "terminate"):
                return "", "end"
            if norm in ("q", "quit", "exit"):
                return "", "quit"
            return line, "text"

    pending_annotation = None
    done = False

    try:
        while not done:
            try:
                line = cmd_queue.get_nowait()
            except queue.Empty:
                line = None
            if line is not None:
                norm = line.strip().lower().replace("ｑ", "q")
                if norm in ("q", "quit", "exit"):
                    print("已退出，未保存轨迹。")
                    return 0
                if norm in ("end", "done", "t", "terminate"):
                    cur_shot, _ = rec.capture_state()
                    rec.add_step(
                        {"action": "terminate", "action_params": {"status": "success"}},
                        "任务目标达成，结束",
                        cur_shot,
                    )
                    done = True
                    continue
                if line.strip():
                    pending_annotation = parse_annotation(line)

            gesture = monitor.poll_completed_gesture()
            if not gesture:
                time.sleep(0.2)
                continue
            kind, data = gesture
            print(f"[i] 检测到手势: {kind} @ {data}")

            cur_shot, cur_dump = rec.capture_state()
            act, text, thought = infer_action(kind, data, pending_annotation)
            ctl = "text"
            if act in ("input", "open") and not text:
                text, ctl = read_line(f"  步骤文本 [{act}]: ")
            if ctl == "end":
                rec.add_step(
                    {"action": "terminate", "action_params": {"status": "success"}},
                    "任务目标达成，结束",
                    cur_shot,
                )
                done = True
                break
            if ctl == "quit":
                print("已退出，未保存轨迹。")
                return 0
            action = rec.build_action(act, kind, data, text)
            rec.add_step(action, thought, rec.pending_shot)
            rec.pending_shot = cur_shot
            rec.pending_dump = cur_dump
            pending_annotation = None
            print(f"[✓] 步骤 {rec.steps[-1]['step']} 已记录 ({act})  截图: {rec.steps[-1]['oss_url']}")

            if not thought:
                t, ctl = read_line("  thought: ")
                if ctl == "end":
                    rec.add_step(
                        {"action": "terminate", "action_params": {"status": "success"}},
                        "任务目标达成，结束",
                        cur_shot,
                    )
                    done = True
                elif ctl == "quit":
                    print("已退出，未保存轨迹。")
                    return 0
                elif t:
                    rec.steps[-1]["thought"] = t
    except KeyboardInterrupt:
        print("\n中断，未保存轨迹。")
        return 1
    finally:
        monitor.stop()

    inferred = rec._infer_package()
    if inferred:
        print(f"[i] 前台包名: {inferred}")

    out = rec.save()
    print(f"\n完成！轨迹已保存: {out}")
    print(f"步骤数: {len(rec.steps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())