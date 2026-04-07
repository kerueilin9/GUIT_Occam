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


class DiscoveryPipeline:
    """Owns discovery artifacts, graph building, and draft task generation."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.recorder = ScreenRecorder()
        self.graph = ScreenGraph()
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
        return self.run_dir

    def register_screen(self, screen: ScreenRecord) -> str:
        canonical_screen_id, is_new = self.graph.add_screen(screen)
        if is_new:
            self._require_prepared(self.states_dir).mkdir(parents=True, exist_ok=True)
            self.recorder.save_screen_record(screen, self._require_prepared(self.states_dir))
        return canonical_screen_id

    def register_transition(self, transition: TransitionRecord) -> None:
        self.graph.add_transition(transition)

    def export_graph(self) -> Path:
        graph_path = self._require_prepared(self.run_dir) / "graph.json"
        graph_path.write_text(
            json.dumps(self.graph.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return graph_path

    def generate_tasks(self) -> list[dict[str, Any]]:
        seeds = self.graph.extract_navigation_seeds(
            max_seeds=self.config.task_generation.max_tasks
        )
        synthesizer = TaskSynthesizer(config=self.config, graph=self.graph)
        generated = synthesizer.synthesize(seeds)
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
                    "seed": item.seed.to_dict(),
                }
            )
        return task_paths

    def write_manifest(self) -> Path:
        manifest = {
            "sut": self.config.sut.name,
            "start_url": self.config.sut.start_url,
            "run_id": self.run_id,
            "discovery": self.config.discovery.to_dict() if hasattr(self.config.discovery, "to_dict") else vars(self.config.discovery),
            "task_generation": self.config.task_generation.to_dict() if hasattr(self.config.task_generation, "to_dict") else vars(self.config.task_generation),
            "validation": self.config.validation.to_dict() if hasattr(self.config.validation, "to_dict") else vars(self.config.validation),
        }
        manifest_path = self._require_prepared(self.run_dir) / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    def _require_prepared(self, path: Path | None) -> Path:
        if path is None:
            raise RuntimeError("DiscoveryPipeline.prepare_run() must be called first.")
        return path
