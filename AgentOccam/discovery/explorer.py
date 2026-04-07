"""Explorer policy abstractions for discovery runs."""

from __future__ import annotations

import re

from AgentOccam.discovery.models import ScreenRecord

DESTRUCTIVE_LABEL_RE = re.compile(
    r"\b(delete|remove|destroy|logout|sign out|submit|save|confirm|archive)\b",
    re.IGNORECASE,
)

SAFE_ROLES = {"link", "button", "tab", "menuitem"}


class ExplorerPolicy:
    """Base policy for enumerating candidate actions from a screen."""

    def enumerate_actions(self, screen: ScreenRecord) -> list[str]:
        raise NotImplementedError


class SafeBFSExplorerPolicy(ExplorerPolicy):
    """Conservative action policy for V1 discovery."""

    def enumerate_actions(self, screen: ScreenRecord) -> list[str]:
        actions: list[str] = []
        for element in screen.interactive_elements:
            if element.role not in SAFE_ROLES:
                continue
            if element.label and DESTRUCTIVE_LABEL_RE.search(element.label):
                continue
            actions.append(f"click [{element.element_id}]")
        if screen.metadata.get("can_go_back", True):
            actions.append("go_back")
        return actions
