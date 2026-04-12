"""Runnable discovery executor for SUT exploration."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from AgentOccam.adk_discovery import ADKDiscoveryClient
from AgentOccam.discovery.explorer import SafeBFSExplorerPolicy
from AgentOccam.discovery.llm_helpers import DiscoveryLLMCoordinator
from AgentOccam.discovery.memory import ExplorationMemory
from AgentOccam.discovery.models import DiscoveryRunConfig
from AgentOccam.discovery.pipeline import DiscoveryPipeline
from AgentOccam.discovery.runtime_browser import DiscoveryRuntimeBrowserMixin
from AgentOccam.discovery.runtime_screening import DiscoveryRuntimeScreeningMixin
from AgentOccam.discovery.runtime_strategy import DiscoveryRuntimeStrategyMixin
from AgentOccam.logger import logger


class DiscoveryDependencyError(RuntimeError):
    """Raised when required runtime dependencies are unavailable."""


def _load_browser_env_runtime() -> tuple[type[Any], Callable[[str], Any]]:
    try:
        env_module = import_module("browser_env.envs")
        actions_module = import_module("browser_env.actions")
    except ModuleNotFoundError as exc:
        missing = exc.name or str(exc)
        raise DiscoveryDependencyError(
            "Discovery runtime dependencies are missing. "
            f"Install the required packages from requirements.txt first. Missing module: {missing}"
        ) from exc
    return env_module.ScriptBrowserEnv, actions_module.create_id_based_action


class DiscoveryExecutor(
    DiscoveryRuntimeStrategyMixin,
    DiscoveryRuntimeScreeningMixin,
    DiscoveryRuntimeBrowserMixin,
):
    """Executes a conservative discovery run against an SUT."""

    def __init__(self, config: DiscoveryRunConfig) -> None:
        self.config = config
        self.adk_client = ADKDiscoveryClient(config)
        self.pipeline = DiscoveryPipeline(config, adk_client=self.adk_client)
        self.policy = SafeBFSExplorerPolicy(
            max_candidate_actions_per_screen=config.discovery.max_candidate_actions_per_screen,
            include_go_back=False,
        )
        self.llm = DiscoveryLLMCoordinator(config, adk_client=self.adk_client)
        self.env: Any | None = None
        self.screen_counter = 0
        self.browser_config_path: Path | None = None
        self.start_time_monotonic = 0.0
        self.explored_actions = 0
        self.failed_actions = 0
        self.skipped_shared_chrome = 0
        self.semantic_revisits = 0
        self.llm_revisit_checks = 0
        self.tried_actions_by_screen: dict[str, set[str]] = defaultdict(set)
        self.tried_signatures_by_screen: dict[str, set[str]] = defaultdict(set)
        self.signature_seen_on_screens: dict[str, set[str]] = defaultdict(set)
        self.signature_success_targets: dict[str, set[str]] = defaultdict(set)
        self.signature_attempt_count: dict[str, int] = defaultdict(int)
        self.revealed_action_groups: dict[str, dict[str, Any]] = {}
        self.revealed_branches_detected = 0
        self.revealed_branch_actions_completed = 0
        self.memory = ExplorationMemory(config)
        self._script_browser_env_cls, self._create_id_based_action = _load_browser_env_runtime()

    def run(self) -> dict[str, Any]:
        run_dir = self.pipeline.prepare_run()
        if self.pipeline.run_id is not None:
            self.adk_client.start_run(
                run_id=self.pipeline.run_id,
                run_dir=run_dir,
                mode=self.config.discovery.mode,
            )
            self.adk_client.record_memory(self.memory)
        self.browser_config_path = self._write_browser_config(run_dir)
        self.env = self._build_env()
        self.start_time_monotonic = time.monotonic()
        logger.info(
            "Discovery run started: sut=%s mode=%s time_budget=%sm max_steps=%s output=%s llm_available=%s adk_enabled=%s adk_available=%s",
            self.config.sut.name,
            self.config.discovery.mode,
            self.config.discovery.time_budget_minutes,
            self.config.discovery.max_steps,
            run_dir,
            self.llm.is_available,
            self.adk_client.enabled,
            self.adk_client.available,
        )

        try:
            return self._run_agentic_session(run_dir)
        finally:
            if self.env is not None:
                logger.info("Closing discovery browser environment.")
                self.env.close()


def run_discovery_from_config_path(config_path: str | Path) -> dict[str, Any]:
    try:
        yaml = import_module("yaml")
    except ModuleNotFoundError as exc:
        raise DiscoveryDependencyError(
            "PyYAML is required to load discovery configs. "
            "Install the required packages from requirements.txt first."
        ) from exc
    config_data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    config = DiscoveryRunConfig.from_dict(config_data)
    logger.info("Loaded discovery config from %s", config_path)
    executor = DiscoveryExecutor(config)
    return executor.run()
