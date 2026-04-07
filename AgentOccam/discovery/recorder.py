"""Screen recording helpers for discovery runs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from AgentOccam.discovery.models import InteractiveElement, ScreenRecord

INTERACTIVE_ELEMENT_RE = re.compile(
    r"^\[(?P<element_id>\d+)\]\s+(?P<role>[A-Za-z_]+)(?:\s+['\"](?P<label>[^'\"]+)['\"])?",
    re.MULTILINE,
)


def normalize_url_for_fingerprint(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


class ScreenRecorder:
    """Builds stable screen records from browser observations."""

    def extract_interactive_elements(self, observation_text: str) -> list[InteractiveElement]:
        elements: list[InteractiveElement] = []
        for match in INTERACTIVE_ELEMENT_RE.finditer(observation_text or ""):
            raw_text = match.group(0).strip()
            elements.append(
                InteractiveElement(
                    element_id=match.group("element_id"),
                    role=(match.group("role") or "").lower(),
                    label=match.group("label") or "",
                    raw_text=raw_text,
                )
            )
        return elements

    def compute_fingerprint(
        self,
        url: str,
        title: str,
        observation_text: str,
        interactive_elements: list[InteractiveElement],
    ) -> str:
        payload = {
            "url": normalize_url_for_fingerprint(url),
            "title": (title or "").strip().lower(),
            "observation_excerpt": "\n".join((observation_text or "").splitlines()[:80]),
            "interactive_elements": [
                {"role": item.role, "label": item.label}
                for item in interactive_elements[:50]
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
        fingerprint = self.compute_fingerprint(
            url=url,
            title=title,
            observation_text=observation_text,
            interactive_elements=interactive_elements,
        )
        return ScreenRecord(
            screen_id=screen_id,
            url=url,
            title=title,
            observation_text=observation_text,
            fingerprint=fingerprint,
            interactive_elements=interactive_elements,
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
