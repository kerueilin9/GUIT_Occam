"""Explorer policy abstractions for discovery runs."""

from __future__ import annotations

import re
from typing import Any

from AgentOccam.discovery.models import ActionCandidate, ScreenRecord

DESTRUCTIVE_LABEL_RE = re.compile(
    r"\b(delete|remove|destroy|logout|sign out|submit|save|confirm|archive|cancel|revoke|approve|reject|deny|reset|restore)\b",
    re.IGNORECASE,
)

SAFE_ROLES = {"link", "button", "tab", "menuitem"}
FOOTER_LABEL_RE = re.compile(
    r"\b(privacy|cookie|search terms|contact us|report all bugs|application|mailto:|copyright|help us keep)\b",
    re.IGNORECASE,
)
HEADER_LABEL_RE = re.compile(
    r"\b(calendar|team view|employees|dashboard|home|management|settings|me)\b",
    re.IGNORECASE,
)


class ExplorerPolicy:
    """Base policy for enumerating candidate actions from a screen."""

    def enumerate_actions(self, screen: ScreenRecord) -> list[ActionCandidate]:
        raise NotImplementedError


class SafeBFSExplorerPolicy(ExplorerPolicy):
    """Conservative action policy for V1 discovery."""

    def __init__(self, max_candidate_actions_per_screen: int = 12, include_go_back: bool = False) -> None:
        self.max_candidate_actions_per_screen = max(1, int(max_candidate_actions_per_screen))
        self.include_go_back = include_go_back

    def enumerate_actions(self, screen: ScreenRecord) -> list[ActionCandidate]:
        actions: list[ActionCandidate] = []
        page_bottom = self._estimate_page_bottom(screen)
        for element in screen.interactive_elements:
            if element.role not in SAFE_ROLES:
                continue
            if element.label and DESTRUCTIVE_LABEL_RE.search(element.label):
                continue
            zone = self._infer_zone(
                screen=screen,
                element_id=element.element_id,
                label=element.label,
                page_bottom=page_bottom,
            )
            signature = self._build_signature(
                role=element.role,
                label=element.label,
                zone=zone,
            )
            actions.append(
                ActionCandidate(
                    action=f"click [{element.element_id}]",
                    element_id=element.element_id,
                    role=element.role,
                    label=element.label,
                    raw_text=element.raw_text,
                    source="interactive_element",
                    zone=zone,
                    signature=signature,
                    is_shared_chrome=(zone != "main"),
                    selected_by="policy",
                )
            )
            if len(actions) >= self.max_candidate_actions_per_screen:
                break
        if self.include_go_back and screen.metadata.get("can_go_back", True):
            actions.append(
                ActionCandidate(
                    action="go_back",
                    role="navigation",
                    label="Go Back",
                    source="navigation",
                    zone="navigation",
                    signature="navigation:go_back",
                    selected_by="policy",
                )
            )
        return actions

    def _infer_zone(
        self,
        screen: ScreenRecord,
        element_id: str,
        label: str,
        page_bottom: float,
    ) -> str:
        bounds = self._get_element_bounds(screen, element_id)
        normalized_label = self._normalize_label(label)

        if normalized_label and FOOTER_LABEL_RE.search(normalized_label):
            return "footer"
        if normalized_label and HEADER_LABEL_RE.search(normalized_label):
            if bounds is None or bounds[1] < 180:
                return "header"

        if bounds is None:
            return "main"

        x, y, width, height = bounds
        bottom = y + height

        if y <= 160:
            return "header"
        if page_bottom > 0 and bottom >= max(page_bottom - 140, y):
            return "footer"
        if x <= 260 and y <= 950:
            return "sidebar"
        return "main"

    def _get_element_bounds(self, screen: ScreenRecord, element_id: str) -> tuple[float, float, float, float] | None:
        obs_nodes = self._get_obs_nodes_info(screen)
        entry = obs_nodes.get(str(element_id), {})
        bounds = entry.get("union_bound")
        if not isinstance(bounds, list) or len(bounds) < 4:
            return None
        try:
            return tuple(float(value) for value in bounds[:4])  # type: ignore[return-value]
        except Exception:
            return None

    def _estimate_page_bottom(self, screen: ScreenRecord) -> float:
        max_bottom = 0.0
        for entry in self._get_obs_nodes_info(screen).values():
            bounds = entry.get("union_bound")
            if not isinstance(bounds, list) or len(bounds) < 4:
                continue
            try:
                y = float(bounds[1])
                height = float(bounds[3])
            except Exception:
                continue
            max_bottom = max(max_bottom, y + height)
        return max_bottom

    def _get_obs_nodes_info(self, screen: ScreenRecord) -> dict[str, Any]:
        observation_metadata = screen.metadata.get("observation_metadata", {})
        if not isinstance(observation_metadata, dict):
            return {}
        text_meta = observation_metadata.get("text", {})
        if not isinstance(text_meta, dict):
            return {}
        obs_nodes_info = text_meta.get("obs_nodes_info", {})
        if isinstance(obs_nodes_info, dict):
            return obs_nodes_info
        return {}

    def _build_signature(self, role: str, label: str, zone: str) -> str:
        normalized_label = self._normalize_label(label) or "unlabeled"
        return f"{zone}:{role}:{normalized_label}"

    def _normalize_label(self, label: str) -> str:
        cleaned = re.sub(r"\s+", " ", (label or "").strip().lower())
        cleaned = re.sub(r"[^\w\s/-]+", "", cleaned)
        return cleaned.strip()
