import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    adb_path: str = os.environ.get("ADB_PATH", "adb")
    device: str = ""  # 为空则自动选择唯一设备
    output_dir: Path = Path(os.environ.get("GUI_COLLECT_OUTPUT", "output"))
    screenshots_dir_name: str = "screenshots"
    tag_prefix: str = "2026-0819-android"
    model: str = os.environ.get("GUI_COLLECT_MODEL", "android-controller/1.3")
    data_source: str = "roinn_human_annotation"

    def resolve(self) -> "Config":
        self.output_dir = self.output_dir.resolve()
        return self