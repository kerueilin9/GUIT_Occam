"""Compact exploration memory for agentic discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from AgentOccam.discovery.action_summary import summarize_candidate_action
from AgentOccam.discovery.models import ActionCandidate, DiscoveryRunConfig, ScreenRecord


@dataclass
class ExplorationEvent:
    event_type: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "summary": self.summary,
            "metadata": self.metadata,
        }


class ExplorationMemory:
    """Stores concise state for LLM-guided exploration."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.events: list[ExplorationEvent] = []
        self.visited_screen_order: list[str] = []
        self.screen_visit_count: dict[str, int] = {}
        self.screen_summaries: dict[str, str] = {}
        self.screen_page_types: dict[str, str] = {}
        self.screen_task_opportunities: dict[str, list[str]] = {}
        self.completed_screens: set[str] = set()
        self.low_value_signatures: set[str] = set()
        self.no_change_signatures: set[str] = set()
        self.known_page_signatures: set[str] = set()
        self.signature_attempts: dict[str, int] = {}
        self.signature_last_effect: dict[str, str] = {}
        self.route_visit_count: dict[str, int] = {}
        self.semantic_revisit_count = 0
        self.semantic_revisit_notes: list[str] = []

    def register_screen(self, screen: ScreenRecord, is_new: bool) -> None:
        self.screen_visit_count[screen.screen_id] = self.screen_visit_count.get(screen.screen_id, 0) + 1
        route = self._normalize_route(screen.url)
        if route:
            self.route_visit_count[route] = self.route_visit_count.get(route, 0) + 1
        if is_new:
            self.visited_screen_order.append(screen.screen_id)
            self.screen_summaries[screen.screen_id] = screen.summary
            self.screen_page_types[screen.screen_id] = screen.page_type
            self.screen_task_opportunities[screen.screen_id] = screen.task_opportunities[:]
            self.events.append(
                ExplorationEvent(
                    event_type="screen_discovered",
                    summary=(
                        f'Discovered screen {screen.screen_id}: '
                        f'{screen.title or screen.url} ({screen.page_type or "page"})'
                    ),
                    metadata={
                        "screen_id": screen.screen_id,
                        "title": screen.title,
                        "url": screen.url,
                        "page_type": screen.page_type,
                    },
                )
            )

    def register_revisit(
        self,
        canonical_screen: ScreenRecord,
        incoming_screen: ScreenRecord,
        reason: str,
        confidence: float = 0.0,
    ) -> None:
        self.screen_visit_count[canonical_screen.screen_id] = self.screen_visit_count.get(canonical_screen.screen_id, 0) + 1
        route = self._normalize_route(canonical_screen.url)
        if route:
            self.route_visit_count[route] = self.route_visit_count.get(route, 0) + 1
        self.semantic_revisit_count += 1
        note = (
            f'{incoming_screen.title or incoming_screen.url} matched visited '
            f'{canonical_screen.screen_id} ({canonical_screen.page_type or canonical_screen.title or canonical_screen.url})'
        )
        self.semantic_revisit_notes.append(note)
        self.semantic_revisit_notes = self.semantic_revisit_notes[-8:]
        self.events.append(
            ExplorationEvent(
                event_type="screen_revisit",
                summary=f"Semantic revisit: {note}",
                metadata={
                    "canonical_screen_id": canonical_screen.screen_id,
                    "incoming_screen_id": incoming_screen.screen_id,
                    "reason": reason,
                    "confidence": confidence,
                },
            )
        )

    def register_attempt(self, candidate: ActionCandidate) -> None:
        if candidate.signature:
            self.signature_attempts[candidate.signature] = self.signature_attempts.get(candidate.signature, 0) + 1

    def register_transition(
        self,
        source_screen: ScreenRecord,
        destination_screen: ScreenRecord,
        candidate: ActionCandidate,
        effect: str,
        success: bool,
    ) -> None:
        action_summary = summarize_candidate_action(candidate)
        if candidate.signature:
            self.signature_last_effect[candidate.signature] = effect
            if effect in {"no_change", "low_value", "known_page"}:
                self.no_change_signatures.add(candidate.signature)
                self.low_value_signatures.add(candidate.signature)
            if effect == "known_page":
                self.known_page_signatures.add(candidate.signature)

        effect_summary = (
            f'{action_summary} from "{source_screen.title or source_screen.url}" '
            f'-> "{destination_screen.title or destination_screen.url}" ({effect})'
        )
        if not success:
            effect_summary = (
                f'{action_summary} from "{source_screen.title or source_screen.url}" failed'
            )
        self.events.append(
            ExplorationEvent(
                event_type="transition",
                summary=effect_summary,
                metadata={
                    "source_screen_id": source_screen.screen_id,
                    "destination_screen_id": destination_screen.screen_id,
                    "action_summary": action_summary,
                    "signature": candidate.signature,
                    "effect": effect,
                    "success": success,
                },
            )
        )

    def mark_screen_done(self, screen: ScreenRecord, reason: str) -> None:
        if screen.screen_id in self.completed_screens:
            return
        self.completed_screens.add(screen.screen_id)
        self.events.append(
            ExplorationEvent(
                event_type="screen_completed",
                summary=f'Screen {screen.screen_id} marked complete: {reason}',
                metadata={"screen_id": screen.screen_id, "reason": reason},
            )
        )

    def summarize_for_prompt(self, current_screen: ScreenRecord) -> str:
        recent_events = self.events[-self.config.llm.memory_recent_events :]
        discovered_lines = []
        for screen_id in self.visited_screen_order[-8:]:
            screen_summary = self.screen_summaries.get(screen_id) or "No summary"
            screen_type = self.screen_page_types.get(screen_id) or "page"
            opportunities = self.screen_task_opportunities.get(screen_id, [])
            discovered_lines.append(
                f'- {screen_id} ({screen_type}) {screen_summary}'
                + (f' | task ideas: {opportunities[:2]}' if opportunities else "")
            )

        event_lines = [f'- {event.summary}' for event in recent_events]
        low_value_lines = sorted(self.low_value_signatures)[:10]
        route_lines = [
            f"- {route} visited {count} time(s)"
            for route, count in sorted(
                self.route_visit_count.items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ]
        revisit_lines = [f"- {item}" for item in self.semantic_revisit_notes[-5:]]

        return "\n".join(
            [
                "Discovered screens:",
                "\n".join(discovered_lines) if discovered_lines else "- None",
                "",
                "Recent exploration events:",
                "\n".join(event_lines) if event_lines else "- None",
                "",
                "Known low-value or no-change signatures:",
                "\n".join(f"- {item}" for item in low_value_lines) if low_value_lines else "- None",
                "",
                "Frequently revisited routes:",
                "\n".join(route_lines) if route_lines else "- None",
                "",
                "Recent semantic revisits:",
                "\n".join(revisit_lines) if revisit_lines else "- None",
                "",
                f"Current screen: {current_screen.screen_id} ({current_screen.title or current_screen.url})",
                f"Completed screens: {len(self.completed_screens)} / {len(self.visited_screen_order)}",
                f"Semantic revisits collapsed: {self.semantic_revisit_count}",
            ]
        )

    def build_compact_snapshot(self, current_screen: ScreenRecord | None = None) -> dict[str, Any]:
        discovered = []
        for screen_id in self.visited_screen_order[-8:]:
            discovered.append(
                {
                    "screen_id": screen_id,
                    "page_type": self.screen_page_types.get(screen_id, ""),
                    "summary": self.screen_summaries.get(screen_id, ""),
                    "task_opportunities": self.screen_task_opportunities.get(screen_id, [])[:2],
                    "visit_count": self.screen_visit_count.get(screen_id, 0),
                }
            )

        recent_events = [event.to_dict() for event in self.events[-self.config.llm.memory_recent_events :]]
        frequent_routes = [
            {
                "route": route,
                "visits": count,
            }
            for route, count in sorted(
                self.route_visit_count.items(),
                key=lambda item: (-item[1], item[0]),
            )[:8]
        ]
        snapshot = {
            "visited_screen_count": len(self.visited_screen_order),
            "completed_screen_count": len(self.completed_screens),
            "semantic_revisit_count": self.semantic_revisit_count,
            "discovered_screens": discovered,
            "recent_events": recent_events,
            "known_low_value_signatures": sorted(self.low_value_signatures)[:12],
            "no_change_signatures": sorted(self.no_change_signatures)[:12],
            "known_page_signatures": sorted(self.known_page_signatures)[:12],
            "frequently_revisited_routes": frequent_routes,
            "recent_semantic_revisits": self.semantic_revisit_notes[-5:],
        }
        if current_screen is not None:
            snapshot["current_screen"] = {
                "screen_id": current_screen.screen_id,
                "title": current_screen.title,
                "url": current_screen.url,
                "page_type": current_screen.page_type,
                "summary": current_screen.summary,
            }
        return snapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "visited_screen_order": self.visited_screen_order[:],
            "screen_visit_count": dict(self.screen_visit_count),
            "screen_summaries": dict(self.screen_summaries),
            "screen_page_types": dict(self.screen_page_types),
            "screen_task_opportunities": {
                screen_id: items[:]
                for screen_id, items in self.screen_task_opportunities.items()
            },
            "completed_screens": sorted(self.completed_screens),
            "low_value_signatures": sorted(self.low_value_signatures),
            "no_change_signatures": sorted(self.no_change_signatures),
            "known_page_signatures": sorted(self.known_page_signatures),
            "signature_attempts": dict(self.signature_attempts),
            "signature_last_effect": dict(self.signature_last_effect),
            "route_visit_count": dict(self.route_visit_count),
            "semantic_revisit_count": self.semantic_revisit_count,
            "semantic_revisit_notes": self.semantic_revisit_notes[:],
        }

    def _normalize_route(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return ""
        return parsed.path or "/"
