import json
import os
import re


class AXTraceRecorder:
    def __init__(self, task_name, output_root="AgentOccam-ScreamShot"):
        self.task_name = _safe_name(task_name)
        self.output_root = output_root
        self.task_dir = os.path.join(output_root, self.task_name)
        self.json_path = os.path.join(output_root, f"{self.task_name}.json")
        self.normalized_ax_tree = []
        self.raw_ax_tree = []
        self.step = 0
        os.makedirs(self.task_dir, exist_ok=True)

    def capture(self, page):
        screenshot_path = os.path.join(self.task_dir, f"step_{self.step:03d}.png")
        raw_tree = page.accessibility.snapshot(interesting_only=False) or {}
        raw_text = _format_ax_tree(raw_tree, normalize=False)
        normalized_text = _format_ax_tree(raw_tree, normalize=True)

        try:
            page.screenshot(path=screenshot_path, full_page=True, timeout=60000)
        except Exception as e:
            screenshot_path = ""
            print(f"[AXTrace] Screenshot failed at step {self.step}: {e}")

        row_base = {
            "step": self.step,
            "url": _normalize_url(page.url),
            "screenshot_path": screenshot_path,
        }
        self.normalized_ax_tree.append({
            **row_base,
            "normalized_ax_tree": normalized_text,
        })
        self.raw_ax_tree.append({
            **row_base,
            "raw_ax_tree": raw_text,
        })
        self._write()
        self.step += 1

    def _write(self):
        os.makedirs(self.output_root, exist_ok=True)
        data = {
            "task_id": self.task_name,
            "task_name": self.task_name,
            "final_url": self.normalized_ax_tree[-1]["url"] if self.normalized_ax_tree else "",
            "normalized_ax_tree": self.normalized_ax_tree,
            "raw_ax_tree": self.raw_ax_tree,
        }
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _safe_name(name):
    name = str(name or "task").strip()
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("_") or "task"


def _normalize_url(url):
    if not url:
        return ""
    return url.split("#", 1)[0].split("?", 1)[0]


def _normalize_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _format_ax_tree(node, normalize=False, depth=0):
    if not isinstance(node, dict):
        return ""

    role = _normalize_text(node.get("role", "unknown"))
    name = _normalize_text(node.get("name", ""))
    if normalize and _skip_node(role, name):
        return _format_children(node, normalize, depth)

    props = []
    for key in ("value", "checked", "selected", "expanded", "pressed", "required", "disabled"):
        if key in node and node[key] is not None:
            value = _normalize_text(node[key]) if isinstance(node[key], str) else node[key]
            props.append(f"{key}: {value}")
    if not normalize and "focused" in node and node["focused"] is not None:
        props.append(f"focused: {node['focused']}")

    line = f'{"  " * depth}{role} "{name}"'
    if props:
        line += " " + " ".join(props)

    children = _format_children(node, normalize, depth + 1)
    return f"{line}\n{children}" if children else line


def _format_children(node, normalize, depth):
    lines = [
        _format_ax_tree(child, normalize=normalize, depth=depth)
        for child in node.get("children", []) or []
    ]
    return "\n".join(line for line in lines if line.strip())


def _skip_node(role, name):
    if role in {"none", "generic"} and not name:
        return True
    if role in {"image", "StaticText", "text"} and not name:
        return True
    if role == "LayoutTable":
        return True
    return False
