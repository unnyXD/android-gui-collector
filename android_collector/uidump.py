import re
import xml.etree.ElementTree as ET


def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))


class UiDump:
    def __init__(self, xml: str):
        self.xml = xml
        self.width = 0
        self.height = 0
        self.nodes: list[dict] = []
        self._parse()

    def _parse(self):
        try:
            root = ET.fromstring(self.xml)
        except ET.ParseError:
            return
        try:
            self.width = int(root.attrib.get("width", "0"))
            self.height = int(root.attrib.get("height", "0"))
        except ValueError:
            pass
        for el in root.iter("node"):
            bounds = _parse_bounds(el.attrib.get("bounds", ""))
            if bounds is None:
                continue
            self.nodes.append(
                {
                    "bounds": bounds,
                    "text": el.attrib.get("text", ""),
                    "content_desc": el.attrib.get("content-desc", ""),
                    "class": el.attrib.get("class", ""),
                    "clickable": el.attrib.get("clickable", "false") == "true",
                    "package": el.attrib.get("package", ""),
                    "resource_id": el.attrib.get("resource-id", ""),
                }
            )

    def node_containing(self, x: int, y: int, prefer_clickable: bool = True) -> dict | None:
        """找到包含 (x,y) 的最深节点；优先 clickable。"""
        contained = [n for n in self.nodes if n["bounds"][0] <= x <= n["bounds"][2] and n["bounds"][1] <= y <= n["bounds"][3]]
        if not contained:
            return None
        if prefer_clickable:
            for n in sorted(contained, key=lambda n: self._area(n), reverse=False):
                if n["clickable"]:
                    return n
        return max(contained, key=self._area)

    @staticmethod
    def _area(n: dict) -> int:
        b = n["bounds"]
        return max(1, (b[2] - b[0]) * (b[3] - b[1]))

    def normalize_bbox(self, bounds: tuple[int, int, int, int]) -> list[float] | None:
        if self.width <= 0 or self.height <= 0:
            return None
        return [
            round(bounds[0] / self.width, 6),
            round(bounds[1] / self.height, 6),
            round(bounds[2] / self.width, 6),
            round(bounds[3] / self.height, 6),
        ]


def parse_dump(xml: str) -> UiDump:
    return UiDump(xml)