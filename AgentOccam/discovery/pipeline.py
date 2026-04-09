"""Discovery pipeline scaffolding for SUT exploration and task generation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from AgentOccam.discovery.models import DiscoveryRunConfig, ScreenRecord, TransitionRecord
from AgentOccam.discovery.recorder import ScreenRecorder
from AgentOccam.discovery.screen_graph import ScreenGraph
from AgentOccam.discovery.task_synthesizer import TaskSynthesizer
from AgentOccam.logger import logger


class DiscoveryPipeline:
    """Owns discovery artifacts, graph building, and draft task generation."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.recorder = ScreenRecorder()
        self.graph = ScreenGraph()
        self.last_task_seeds: list[dict[str, Any]] = []
        self.last_task_dedup_report: dict[str, Any] = {}
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.screens_dir: Path | None = None
        self.states_dir: Path | None = None
        self.tasks_dir: Path | None = None

    def prepare_run(self, run_id: str | None = None) -> Path:
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(self.config.output_root) / self.run_id
        self.screens_dir = self.run_dir / "screens"
        self.states_dir = self.run_dir / "states"
        self.tasks_dir = self.run_dir / "tasks"

        self.screens_dir.mkdir(parents=True, exist_ok=True)
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.write_manifest()
        logger.info("Prepared discovery run directory: %s", self.run_dir)
        return self.run_dir

    def register_screen(self, screen: ScreenRecord) -> tuple[str, bool, ScreenRecord]:
        canonical_screen_id, is_new = self.graph.add_screen(screen)
        canonical_screen = self.graph.get_screen(canonical_screen_id)
        if is_new:
            self._require_prepared(self.states_dir).mkdir(parents=True, exist_ok=True)
            self.recorder.save_screen_record(canonical_screen, self._require_prepared(self.states_dir))
            logger.debug(
                "Saved screen state: screen=%s is_new=%s state_dir=%s",
                canonical_screen_id,
                is_new,
                self.states_dir,
            )
        else:
            logger.debug(
                "Skipped state export for duplicate screen=%s",
                canonical_screen_id,
            )
        return canonical_screen_id, is_new, canonical_screen

    def register_transition(self, transition: TransitionRecord) -> None:
        self.graph.add_transition(transition)

    def export_graph(self) -> Path:
        graph_path = self._require_prepared(self.run_dir) / "graph.json"
        graph_path.write_text(
            json.dumps(self.graph.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported full graph: %s", graph_path)
        return graph_path

    def export_compact_graph(self) -> Path:
        graph_path = self._require_prepared(self.run_dir) / "graph_compact.json"
        graph_path.write_text(
            json.dumps(self.graph.to_compact_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported compact graph: %s", graph_path)
        return graph_path

    def export_page_overviews(self) -> tuple[Path, Path]:
        page_overview = self.graph.build_page_overviews()
        json_path = self._require_prepared(self.run_dir) / "page_overview.json"
        json_path.write_text(
            json.dumps(page_overview, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = self._require_prepared(self.run_dir) / "page_overview.md"
        md_path.write_text(self._render_page_overview_markdown(page_overview), encoding="utf-8")
        logger.info("Exported page overviews: json=%s md=%s", json_path, md_path)
        return json_path, md_path

    def export_page_families(self) -> tuple[Path, Path]:
        page_families = self.graph.build_page_families()
        json_path = self._require_prepared(self.run_dir) / "page_families.json"
        json_path.write_text(
            json.dumps(page_families, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = self._require_prepared(self.run_dir) / "page_families.md"
        md_path.write_text(self._render_page_family_markdown(page_families), encoding="utf-8")
        logger.info("Exported page families: json=%s md=%s", json_path, md_path)
        return json_path, md_path

    def export_sut_overview(self) -> tuple[Path, Path]:
        overview = self.graph.build_sut_overview()
        json_path = self._require_prepared(self.run_dir) / "sut_overview.json"
        json_path.write_text(
            json.dumps(overview, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = self._require_prepared(self.run_dir) / "sut_overview.md"
        md_path.write_text(self._render_sut_overview_markdown(overview), encoding="utf-8")
        logger.info("Exported SUT overview: json=%s md=%s", json_path, md_path)
        return json_path, md_path

    def export_sut_dossier(self) -> tuple[Path, Path]:
        dossier = self.graph.build_sut_dossier(self.config.sut.name)
        json_path = self._require_prepared(self.run_dir) / "sut_dossier.json"
        json_path.write_text(
            json.dumps(dossier, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = self._require_prepared(self.run_dir) / "sut_dossier.md"
        md_path.write_text(self._render_sut_dossier_markdown(dossier), encoding="utf-8")
        logger.info("Exported SUT dossier: json=%s md=%s", json_path, md_path)
        return json_path, md_path

    def generate_tasks(self) -> list[dict[str, Any]]:
        seeds = self.graph.extract_task_seeds(
            max_seeds=self.config.task_generation.max_tasks,
            max_tasks_per_screen=self.config.task_generation.max_tasks_per_family,
        )
        self.last_task_seeds = [seed.to_dict() for seed in seeds]
        self.export_task_plan()
        self.export_task_seeds()
        logger.info("Extracted %s task seeds for generation.", len(seeds))
        synthesizer = TaskSynthesizer(config=self.config, graph=self.graph)
        generated = synthesizer.synthesize(seeds)
        self.last_task_dedup_report = synthesizer.last_dedup_report
        self.export_task_dedup_report()
        task_paths: list[dict[str, Any]] = []
        for item in generated:
            target = self._require_prepared(self.tasks_dir) / f"{item.candidate.task_id}.json"
            target.write_text(
                json.dumps(item.candidate.task_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            task_paths.append(
                {
                    "task_id": item.candidate.task_id,
                    "task_path": str(target),
                    "generation_method": item.candidate.generation_method,
                    "confidence": item.candidate.confidence,
                    "review_notes": item.candidate.review_notes,
                    "seed": item.seed.to_dict(),
                }
            )
            logger.debug("Wrote generated task: %s", target)
        logger.info("Generated %s task configs.", len(task_paths))
        return task_paths

    def export_task_seeds(self) -> Path:
        target = self._require_prepared(self.run_dir) / "task_seeds.json"
        target.write_text(
            json.dumps(self.last_task_seeds, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported task seeds: %s", target)
        return target

    def export_task_plan(self) -> Path:
        target = self._require_prepared(self.run_dir) / "task_plan.json"
        target.write_text(
            json.dumps(self.last_task_seeds, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported task plan: %s", target)
        return target

    def export_task_dedup_report(self) -> Path:
        target = self._require_prepared(self.run_dir) / "task_dedup_report.json"
        target.write_text(
            json.dumps(self.last_task_dedup_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported task dedup report: %s", target)
        return target

    def write_manifest(self) -> Path:
        manifest = {
            "sut": self.config.sut.name,
            "start_url": self.config.sut.start_url,
            "run_id": self.run_id,
            "discovery": self.config.discovery.to_dict() if hasattr(self.config.discovery, "to_dict") else vars(self.config.discovery),
            "llm": self.config.llm.to_dict() if hasattr(self.config.llm, "to_dict") else vars(self.config.llm),
            "task_generation": self.config.task_generation.to_dict() if hasattr(self.config.task_generation, "to_dict") else vars(self.config.task_generation),
            "validation": self.config.validation.to_dict() if hasattr(self.config.validation, "to_dict") else vars(self.config.validation),
        }
        manifest_path = self._require_prepared(self.run_dir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("Wrote run manifest: %s", manifest_path)
        return manifest_path

    def write_summary(self, summary: dict[str, Any]) -> Path:
        summary_path = self._require_prepared(self.run_dir) / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote discovery summary: %s", summary_path)
        return summary_path

    def _render_sut_overview_markdown(self, overview: dict[str, Any]) -> str:
        lines = [
            "# SUT Overview",
            "",
            overview.get("overview_text", "").strip() or overview.get("purpose", "").strip(),
            "",
            "## Feature Areas",
        ]
        for item in overview.get("feature_areas", []):
            lines.append(f'- {item["page_type"]} ({item["count"]} screens)')
        lines.extend(["", "## Domain Objects"])
        for item in overview.get("domain_objects", []):
            lines.append(f'- {item["name"]} ({item["count"]})')
        lines.extend(["", "## Core Capabilities"])
        for item in overview.get("core_capabilities", []):
            lines.append(f"- {item}")
        lines.extend(["", "## Form-Heavy Pages"])
        for item in overview.get("form_pages", []):
            page_id = item.get("family_id") or item.get("screen_id") or "unknown"
            page_name = item.get("page_name") or item.get("page_type") or "Unknown Page"
            lines.append(
                f'- {page_id}: {page_name} ({item["form_field_count"]} fields)'
            )
        return "\n".join(lines).strip() + "\n"

    def _render_page_overview_markdown(self, page_overview: list[dict[str, Any]]) -> str:
        lines = ["# Page Overview", ""]
        for page in page_overview:
            lines.extend(
                [
                    f'## {page["family_id"]} - {page["name"]}',
                    f'- Page Type: {page["page_type"] or "(unknown)"}',
                    f'- Routes: {", ".join(page.get("route_patterns", [])) or "/"}',
                    f'- Summary: {page["summary"] or "No summary"}',
                    f'- Domain Objects: {", ".join(page.get("domain_objects", [])) or "None"}',
                    f'- Supported Operations: {", ".join(page.get("supported_operations", [])) or "None"}',
                    f'- Important DOM: {", ".join(page.get("important_dom", [])) or "None"}',
                    f'- Secondary DOM: {", ".join(page.get("secondary_dom", [])) or "None"}',
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    def _render_page_family_markdown(self, page_families: list[dict[str, Any]]) -> str:
        lines = ["# Page Families", ""]
        for page in page_families:
            lines.extend(
                [
                    f'## {page["family_id"]} - {page["name"]}',
                    f'- Canonical Screen: {page["canonical_screen_id"]}',
                    f'- Routes: {", ".join(page.get("route_patterns", [])) or "/"}',
                    f'- Summary: {page.get("summary") or page.get("purpose") or "No summary"}',
                    f'- Supported Operations: {", ".join(page.get("supported_operations", [])) or "None"}',
                    f'- Task Opportunities: {", ".join(page.get("task_opportunities", [])) or "None"}',
                    f'- Important DOM: {", ".join(page.get("important_dom", [])) or "None"}',
                    f'- Forms: {", ".join(field.get("label", "") for field in page.get("form_fields", []) if field.get("label")) or "None"}',
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

    def _render_sut_dossier_markdown(self, dossier: dict[str, Any]) -> str:
        lines = [
            "# SUT Dossier",
            "",
            dossier.get("overview_text", "").strip() or dossier.get("purpose", "").strip(),
            "",
            "## Page Families",
        ]
        for family in dossier.get("page_families", [])[:20]:
            lines.append(
                f'- {family["family_id"]}: {family["name"]} ({", ".join(family.get("supported_operations", [])) or "no ops"})'
            )
        lines.extend(["", "## Domain Objects"])
        for item in dossier.get("domain_objects", []):
            lines.append(f'- {item["name"]} ({item["count"]})')
        lines.extend(["", "## Core Capabilities"])
        for item in dossier.get("core_capabilities", []):
            lines.append(f"- {item}")
        return "\n".join(lines).strip() + "\n"

    def _require_prepared(self, path: Path | None) -> Path:
        if path is None:
            raise RuntimeError("DiscoveryPipeline.prepare_run() must be called first.")
        return path
