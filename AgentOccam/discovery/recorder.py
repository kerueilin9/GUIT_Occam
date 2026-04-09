"""Screen recording helpers for discovery runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from AgentOccam.discovery.models import FormField, InteractiveElement, ScreenRecord

INTERACTIVE_ELEMENT_RE = re.compile(
    r"^\s*\[(?P<element_id>\d+)\]\s+(?P<role>[A-Za-z_]+)(?:\s+['\"](?P<label>[^'\"]*)['\"])?",
    re.MULTILINE,
)
ELEMENT_ID_RE = re.compile(r"\[\d+\]")
SALIENT_ROLE_RE = re.compile(
    r"\b(rootwebarea|dialog|alertdialog|form|heading|textbox|combobox|textarea|searchbox|listbox|switch|checkbox|radio|radiobutton|button|link|menuitem|tab|tabpanel|alert|status)\b",
    re.IGNORECASE,
)
FORM_FIELD_ROLES = {
    "textbox",
    "combobox",
    "checkbox",
    "radio",
    "radiobutton",
    "spinbutton",
    "searchbox",
    "textarea",
    "listbox",
    "switch",
}
REQUIRED_RE = re.compile(r"\brequired(?::\s*true)?\b", re.IGNORECASE)


def normalize_url_for_fingerprint(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def normalize_observation_for_fingerprint(observation_text: str) -> str:
    raw_lines = [raw_line for raw_line in (observation_text or "").splitlines() if raw_line.strip()]
    normalized_lines = [ELEMENT_ID_RE.sub("[id]", raw_line).strip() for raw_line in raw_lines]
    first_lines = normalized_lines[:120]
    last_lines = normalized_lines[-60:]
    salient_lines = [
        line
        for line in normalized_lines
        if SALIENT_ROLE_RE.search(line)
    ][:120]

    combined: list[str] = []
    seen: set[str] = set()
    for line in first_lines + salient_lines + last_lines:
        if not line or line in seen:
            continue
        seen.add(line)
        combined.append(line)
    return "\n".join(combined[:240])


class ScreenRecorder:
    """Builds stable screen records from browser observations."""

    def _parse_required(self, raw_text: str) -> bool:
        lowered = raw_text.lower()
        if "required: false" in lowered:
            return False
        if "required: true" in lowered:
            return True
        return bool(REQUIRED_RE.search(raw_text))

    def extract_interactive_elements(self, observation_text: str) -> list[InteractiveElement]:
        elements: list[InteractiveElement] = []
        for raw_line in (observation_text or "").splitlines():
            match = INTERACTIVE_ELEMENT_RE.match(raw_line)
            if not match:
                continue
            raw_text = raw_line.strip()
            elements.append(
                InteractiveElement(
                    element_id=match.group("element_id"),
                    role=(match.group("role") or "").lower(),
                    label=match.group("label") or "",
                    raw_text=raw_text,
                )
            )
        return elements

    def extract_form_fields(self, observation_text: str) -> list[FormField]:
        fields: list[FormField] = []
        for raw_line in (observation_text or "").splitlines():
            match = INTERACTIVE_ELEMENT_RE.match(raw_line)
            if not match:
                continue
            role = (match.group("role") or "").lower()
            if role not in FORM_FIELD_ROLES:
                continue
            raw_text = raw_line.strip()
            label = (match.group("label") or "").strip()
            fields.append(
                FormField(
                    element_id=match.group("element_id"),
                    label=label,
                    field_type=role,
                    required=self._parse_required(raw_text),
                    raw_text=raw_text,
                )
            )
        return fields

    def compute_fingerprint(
        self,
        url: str,
        title: str,
        observation_text: str,
        interactive_elements: list[InteractiveElement],
        form_fields: list[FormField],
    ) -> str:
        payload = {
            "url": normalize_url_for_fingerprint(url),
            "title": (title or "").strip().lower(),
            "observation_excerpt": normalize_observation_for_fingerprint(observation_text),
            "interactive_elements": [
                {"role": item.role, "label": item.label}
                for item in interactive_elements[:120]
            ],
            "form_fields": [
                {
                    "label": field.label,
                    "type": field.field_type,
                    "required": field.required,
                }
                for field in form_fields[:40]
            ],
        }
        serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def build_screen_record(
        self,
        screen_id: str,
        url: str,
        title: str,
        observation_text: str,
        screenshot_path: str | None = None,
        metadata: dict | None = None,
    ) -> ScreenRecord:
        interactive_elements = self.extract_interactive_elements(observation_text)
        form_fields = self.extract_form_fields(observation_text)
        fingerprint = self.compute_fingerprint(
            url=url,
            title=title,
            observation_text=observation_text,
            interactive_elements=interactive_elements,
            form_fields=form_fields,
        )
        return ScreenRecord(
            screen_id=screen_id,
            url=url,
            title=title,
            observation_text=observation_text,
            fingerprint=fingerprint,
            interactive_elements=interactive_elements,
            form_fields=form_fields,
            screenshot_path=screenshot_path,
            metadata=metadata or {},
        )

    def save_screen_record(self, screen: ScreenRecord, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"{screen.screen_id}.json"
        target.write_text(
            json.dumps(screen.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target
