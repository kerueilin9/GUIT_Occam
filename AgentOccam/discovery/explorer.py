"""Explorer policy abstractions for discovery runs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlparse

from AgentOccam.discovery.models import ActionCandidate, ScreenRecord

DESTRUCTIVE_LABEL_RE = re.compile(
    r"\b(delete|remove|destroy|log[\s-]?out|sign[\s-]?out|submit|save|confirm|archive|revoke|approve|reject|deny|reset|restore)\b",
    re.IGNORECASE,
)
TEMPORAL_SCREEN_RE = re.compile(
    r"\b(calendar|month|week|day|date)\b",
    re.IGNORECASE,
)
TEMPORAL_PREVIOUS_RE = re.compile(
    r"\b(prev(?:ious)?|back)\b.*\b(month|week|day|year|calendar|period)\b|\b(month|week|day|year)\b.*\b(prev(?:ious)?|back)\b",
    re.IGNORECASE,
)
TEMPORAL_NEXT_RE = re.compile(
    r"\bnext\b.*\b(month|week|day|year|calendar|period)\b|\b(month|week|day|year)\b.*\bnext\b",
    re.IGNORECASE,
)
MONTH_LABEL_RE = re.compile(
    r"^(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:,\s*\d{4})?(?:\s+.+)?$",
    re.IGNORECASE,
)
MONTH_PICKER_RE = re.compile(
    r"^(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?),?\s+\d{4}(?:\s+.+)?$",
    re.IGNORECASE,
)

ACTIONABLE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "listbox",
    "menuitem",
    "radio",
    "radiobutton",
    "searchbox",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
    "textarea",
}
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
    """Thin action adapter from accessibility-tree elements to executable actions."""

    def __init__(self, max_candidate_actions_per_screen: int = 12, include_go_back: bool = False) -> None:
        self.max_candidate_actions_per_screen = max(1, int(max_candidate_actions_per_screen))
        self.include_go_back = include_go_back

    def enumerate_actions(self, screen: ScreenRecord) -> list[ActionCandidate]:
        actions: list[ActionCandidate] = []
        seen_actions: set[str] = set()
        page_bottom = self._estimate_page_bottom(screen)
        temporal_signature_map = self._build_temporal_signature_map(screen)
        for element in screen.interactive_elements:
            if element.role not in ACTIONABLE_ROLES:
                continue
            if self._should_skip_terminal_element(element.label, element.raw_text):
                continue
            action = f"click [{element.element_id}]"
            if action in seen_actions:
                continue
            zone = self._infer_zone(
                screen=screen,
                element_id=element.element_id,
                label=element.label,
                page_bottom=page_bottom,
            )
            signature = temporal_signature_map.get(element.element_id) or self._build_signature(
                role=element.role,
                label=element.label,
                zone=zone,
            )
            actions.append(
                ActionCandidate(
                    action=action,
                    element_id=element.element_id,
                    role=element.role,
                    label=element.label,
                    raw_text=element.raw_text,
                    source="interactive_element",
                    zone=zone,
                    signature=signature,
                    is_shared_chrome=(zone != "main"),
                    selected_by="adapter",
                )
            )
            seen_actions.add(action)
        if self.include_go_back and screen.metadata.get("can_go_back", True):
            actions.append(
                ActionCandidate(
                    action="go_back",
                    role="navigation",
                    label="Go Back",
                    source="navigation",
                    zone="navigation",
                    signature="navigation:go_back",
                    selected_by="adapter",
                )
            )
        if len(actions) <= self.max_candidate_actions_per_screen:
            return actions
        main_actions = [item for item in actions if item.zone == "main"]
        secondary_actions = [item for item in actions if item.zone != "main"]
        return (main_actions + secondary_actions)[: self.max_candidate_actions_per_screen]

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

    def _build_temporal_signature_map(self, screen: ScreenRecord) -> dict[str, str]:
        if not self._screen_looks_temporal(screen):
            return {}

        actionable_elements = [
            element
            for element in screen.interactive_elements
            if element.role in ACTIONABLE_ROLES
        ]
        signature_map: dict[str, str] = {}

        for element in actionable_elements:
            explicit_signature = self._explicit_temporal_signature(
                label=element.label,
                raw_text=element.raw_text,
            )
            if explicit_signature:
                signature_map[element.element_id] = explicit_signature

        for index, element in enumerate(actionable_elements):
            if element.element_id in signature_map:
                continue
            if not self._is_month_picker_label(element.label):
                continue
            signature_map[element.element_id] = "temporal:period_picker"
            previous_element = self._find_adjacent_month_control(actionable_elements, index, direction=-1)
            next_element = self._find_adjacent_month_control(actionable_elements, index, direction=1)
            if previous_element is not None and previous_element.element_id not in signature_map:
                signature_map[previous_element.element_id] = "temporal:previous_period"
            if next_element is not None and next_element.element_id not in signature_map:
                signature_map[next_element.element_id] = "temporal:next_period"

        return signature_map

    def _screen_looks_temporal(self, screen: ScreenRecord) -> bool:
        try:
            parsed = urlparse(screen.url)
        except Exception:
            parsed = None
        if parsed is not None:
            path = (parsed.path or "").lower()
            if "calendar" in path or "schedule" in path:
                return True
            query_keys = {
                key.lower()
                for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
            }
            if query_keys & {"date", "month", "year", "week", "day"}:
                return True

        if TEMPORAL_SCREEN_RE.search(screen.title or ""):
            return True

        temporal_controls = 0
        for element in screen.interactive_elements[:64]:
            if self._explicit_temporal_signature(element.label, element.raw_text):
                temporal_controls += 1
                continue
            if self._is_month_picker_label(element.label) or self._looks_like_month_control(element.label):
                temporal_controls += 1
        return temporal_controls >= 2

    def _explicit_temporal_signature(self, label: str, raw_text: str) -> str:
        combined = " ".join(
            part.strip()
            for part in (label or "", raw_text or "")
            if part and part.strip()
        )
        normalized = self._normalize_label(combined)
        if not normalized:
            return ""
        if TEMPORAL_PREVIOUS_RE.search(normalized):
            return "temporal:previous_period"
        if TEMPORAL_NEXT_RE.search(normalized):
            return "temporal:next_period"
        if "today" in normalized or "current period" in normalized:
            return "temporal:current_period"
        return ""

    def _find_adjacent_month_control(
        self,
        elements: list[Any],
        start_index: int,
        direction: int,
    ):
        index = start_index + direction
        steps = 0
        while 0 <= index < len(elements) and steps < 4:
            candidate = elements[index]
            if self._looks_like_month_control(candidate.label):
                return candidate
            index += direction
            steps += 1
        return None

    def _is_month_picker_label(self, label: str) -> bool:
        return bool(MONTH_PICKER_RE.match((label or "").strip()))

    def _looks_like_month_control(self, label: str) -> bool:
        return bool(MONTH_LABEL_RE.match((label or "").strip()))

    def _normalize_label(self, label: str) -> str:
        cleaned = re.sub(r"\s+", " ", (label or "").strip().lower())
        cleaned = re.sub(r"[^\w\s/-]+", "", cleaned)
        return cleaned.strip()

    def _should_skip_terminal_element(self, label: str, raw_text: str) -> bool:
        combined_text = " ".join(
            part.strip()
            for part in (label or "", raw_text or "")
            if part and part.strip()
        )
        return bool(combined_text and DESTRUCTIVE_LABEL_RE.search(combined_text))
