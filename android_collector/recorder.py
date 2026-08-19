import json
import random
import time
from pathlib import Path

from .adb import Adb, AdbError
from .uidump import UiDump, parse_dump


class Recorder:
    def __init__(self, cfg):
        self.cfg = cfg
        self.adb = Adb(cfg.adb_path, cfg.device)
        self.execution_id = ""
        self.tag = ""
        self.goal = ""
        self.app_names = ""
        self.package = ""
        self.package_override = ""
        self.screen_w = 0
        self.screen_h = 0
        self.steps: list[dict] = []
        self._shot_dir: Path | None = None
        self.pending_shot = ""
        self.pending_dump: UiDump | None = None
        self._packages_seen: list[str] = []

    # ---------- 初始化 ----------
    def start_session(self, goal: str, app_names: str, tag: str):
        self.goal = goal
        self.app_names = app_names
        self.tag = tag
        self.execution_id = "batch_" + time.strftime("%Y%m%d%H%M%S") + f"_task_{random.randint(0, 9999):04d}"
        self.package = self._safe_current_app()
        try:
            self.screen_w, self.screen_h = self.adb.screen_size()
        except AdbError:
            self.screen_w, self.screen_h = 0, 0
        self._shot_dir = self.cfg.output_dir / self.execution_id / self.cfg.screenshots_dir_name
        self._shot_dir.mkdir(parents=True, exist_ok=True)

    def _safe_current_app(self) -> str:
        try:
            return self.adb.current_app()
        except AdbError:
            return ""

    # ---------- 截图 / dump ----------
    def capture_state(self) -> tuple[str, UiDump | None]:
        """截图 + dump 当前状态，返回 (相对路径, dump)。"""
        idx = len(self.steps) + 1
        name = f"step_{idx:02d}.png" if idx < 1000 else f"step_{idx}.png"
        shot = self._shot_dir / name
        self.adb.screencap(shot)
        rel = Path(self.execution_id) / self.cfg.screenshots_dir_name / name
        dump = self._try_dump()
        pkg = self._safe_current_app()
        if pkg:
            self._packages_seen.append(pkg)
        return rel.as_posix(), dump

    def _try_dump(self) -> UiDump | None:
        """uiautomator dump 在 MIUI/动画场景常首次失败，重试并校验 XML。"""
        for _ in range(3):
            try:
                xml = self.adb.dump_tree()
                if "<hierarchy" in xml:
                    d = parse_dump(xml, self.screen_w, self.screen_h)
                    if d.nodes:
                        return d
            except AdbError:
                pass
            time.sleep(0.6)
        return None

    # ---------- 包名推断 ----------
    LAUNCHER_HINTS = ("launcher", "mobi.launcher", "trebuchet", "miui.home")

    def _infer_package(self) -> str:
        """统计每步前台包名，取最多的非桌面 App；退化到最后一个非桌面包名。"""
        seen = [p for p in self._packages_seen if p]
        apps = [p for p in seen if not any(h in p.lower() for h in self.LAUNCHER_HINTS)]
        if not apps:
            return seen[-1] if seen else self.package
        from collections import Counter

        return Counter(apps).most_common(1)[0][0]

    # ---------- 组装动作 ----------
    def _norm(self, x: float, y: float) -> list[float]:
        w = (self.pending_dump.width if self.pending_dump else 0) or self.screen_w
        h = (self.pending_dump.height if self.pending_dump else 0) or self.screen_h
        if w <= 0 or h <= 0:
            return [round(x, 6), round(y, 6)]
        return [round(x / w, 6), round(y / h, 6)]

    def build_action(
        self,
        action: str,
        touch_kind: str | None,
        touch_data,
        text: str = "",
    ) -> dict:
        params: dict = {}
        if action == "terminate":
            params["status"] = text if text else "success"
        elif action in ("back", "home"):
            pass
        elif action == "open":
            if text:
                params["text"] = text
            bbox = self._bbox_from_touch(touch_data)
            if bbox:
                params["bbox"] = bbox
        elif action in ("click", "input"):
            pt = self._touch_point(touch_data)
            if pt:
                params["coordinate"] = self._norm(*pt)
            bbox = self._bbox_from_touch(touch_data)
            if bbox:
                params["bbox"] = bbox
            if action == "input" and text:
                params["text"] = text
        elif action == "swipe":
            if touch_kind == "swipe" and touch_data:
                (x1, y1), (x2, y2) = touch_data
                params["start"] = self._norm(x1, y1)
                params["end"] = self._norm(x2, y2)
            else:
                params["start"] = [0.5, 0.8]
                params["end"] = [0.5, 0.2]
        return {"action": action, "action_params": params}

    @staticmethod
    def _touch_point(touch_data) -> tuple[int, int] | None:
        """tap -> (x,y)；swipe -> 取起点。"""
        if not touch_data:
            return None
        if isinstance(touch_data[0], (list, tuple)):
            return int(touch_data[0][0]), int(touch_data[0][1])
        return int(touch_data[0]), int(touch_data[1])

    def _bbox_from_touch(self, touch_data) -> list[float] | None:
        if not self.pending_dump:
            return None
        pt = self._touch_point(touch_data)
        if not pt:
            return None
        x, y = pt
        node = self.pending_dump.node_containing(x, y)
        if not node:
            return None
        return self.pending_dump.normalize_bbox(node["bounds"])

    # ---------- 步骤记录 ----------
    def add_step(self, action: dict, thought: str, shot_rel: str) -> int:
        step_no = len(self.steps) + 1
        self.steps.append(
            {
                "step": step_no,
                "oss_url": shot_rel,
                "thought": thought,
                "action": action,
            }
        )
        return step_no

    # ---------- 输出 ----------
    def save(self, judege_think: str = "") -> Path:
        task_name = self.execution_id
        package = self.package_override or self._infer_package()
        if not judege_think:
            judege_think = (
                "任务完成，终态截图完整展示交付结果，动作序列与截图逐步对应，可回放验证。"
            )
        payload = {
            "execution_id": self.execution_id,
            "task_info": {
                "app_type": "android",
                "app_names": [n.strip() for n in self.app_names.split(",") if n.strip()],
                "task_desc": self.goal,
                "task_name": task_name,
                "task_source_id": task_name,
                "annotation_task_tag": self.tag,
            },
            "tag": self.tag,
            "status": "completed",
            "goal": self.goal,
            "task_name": task_name,
            "app": self.app_names,
            "app_package_names": package,
            "platform": "android",
            "target_language": "cn",
            "data_source": self.cfg.data_source,
            "steps_count": len(self.steps),
            "messages": self.steps,
            "model": self.cfg.model,
            "judege_result": "normal",
            "judege_think": judege_think,
        }
        out = self.cfg.output_dir / self.execution_id / "trajectory.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        return out