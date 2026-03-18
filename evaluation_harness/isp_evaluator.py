"""
ISP (Input Space Partitioning) Evaluator.

Summarises and optionally post-processes an ISP report produced by
:class:`~AgentOccam.AgentOccam.ISPAgentOccam`.

If the underlying task config contains Gherkin acceptance criteria, each run
that reached the ``stop`` action is also evaluated by the existing
:class:`~evaluation_harness.gherkin_evaluator.GherkinEvaluator` logic via
``evaluator_router``.

Usage
-----
>>> from evaluation_harness.isp_evaluator import ISPEvaluator
>>> evaluator = ISPEvaluator(config_file="config_files/keystonejs/task_post01.json")
>>> report    = evaluator.evaluate(isp_results)
"""

from __future__ import annotations

import json
import os
from typing import Any


class ISPEvaluator:
    """
    Post-processes an ISP report dict and adds human-readable summaries.

    Parameters
    ----------
    config_file : str
        Path to the task config JSON.  Used to load Gherkin acceptance
        criteria if present.
    """

    def __init__(self, config_file: str):
        self.config_file = config_file
        with open(config_file, "r", encoding="utf-8") as f:
            self.task_config: dict = json.load(f)

        # Load acceptance criteria (Gherkin tasks)
        gherkin = self.task_config.get("gherkin", {})
        if isinstance(gherkin, dict):
            self.acceptance_criteria: list = (
                self.task_config.get("eval", {})
                    .get("reference_answers", {})
                    .get("gherkin_acceptance_criteria", [])
            )
        else:
            self.acceptance_criteria = []

        # ISP-specific expected outcomes per category
        # e.g. {"original": "pass", "valid": "pass", "empty": "fail"}
        self.isp_acceptance_criteria: dict = (
            self.task_config.get("eval", {})
                .get("reference_answers", {})
                .get("isp_acceptance_criteria", {})
        )
        # Also look in task-level isp block (task_post01_isp.json style)
        if not self.isp_acceptance_criteria:
            self.isp_acceptance_criteria = (
                self.task_config.get("isp", {})
                    .get("expected_outcomes_by_category", {})
            )

    # ── public API ────────────────────────────────────────────────────────────

    def evaluate(self, isp_report: dict) -> dict:
        """
        Annotate *isp_report* with richer evaluation data.

        Currently adds:

        * ``gherkin_criteria`` annotation on each run record (if applicable).
        * A ``by_category`` breakdown in the summary section.

        Parameters
        ----------
        isp_report : dict
            The raw report dict returned by
            :meth:`~ISPAgentOccam.act_isp`.

        Returns
        -------
        dict
            The same dict, mutated in-place (and also returned for
            convenience).
        """
        runs: list = isp_report.get("runs", [])

        # ── per-run annotation ────────────────────────────────────────────────
        for run in runs:
            run["evaluation"] = self._evaluate_run(run)

        # ── aggregate by category ─────────────────────────────────────────────
        by_category: dict = {}
        for run in runs:
            for fid, combo_val in run.get("partition_values", {}).items():
                cat = combo_val.get("category", "unknown")
                by_category.setdefault(cat, {
                    "total": 0, "passed": 0, "failed": 0,
                    "expected": self.isp_acceptance_criteria.get(cat, "unknown"),
                    "verdict": "unknown",
                })
                by_category[cat]["total"]  += 1
                if run.get("passed"):
                    by_category[cat]["passed"] += 1
                else:
                    by_category[cat]["failed"] += 1

        # Compute per-category verdict (majority actual vs. expected)
        for cat, stats in by_category.items():
            expected = stats["expected"]
            if expected == "unknown":
                stats["verdict"] = "unknown"
            else:
                actual_majority = "pass" if stats["passed"] >= stats["failed"] else "fail"
                stats["verdict"] = "correct" if actual_majority == expected else "incorrect"

        isp_report.setdefault("summary", {})["by_category"] = by_category

        # ── overall ISP verdict ──────────────────────────────────────────────────
        if self.isp_acceptance_criteria:
            total_cats   = len(by_category)
            correct_cats = sum(
                1 for s in by_category.values() if s.get("verdict") == "correct"
            )
            isp_report["summary"]["isp_verdict"] = {
                "categories_checked":   total_cats,
                "categories_correct":   correct_cats,
                "categories_incorrect": total_cats - correct_cats,
                "overall": "correct" if correct_cats == total_cats else "incorrect",
            }

        # ── Gherkin acceptance-criteria summary ─────────────────────────────────
        if self.acceptance_criteria:
            isp_report["summary"]["acceptance_criteria"] = (
                self.acceptance_criteria
            )

        return isp_report

    # ── private helpers ───────────────────────────────────────────────────────

    def _evaluate_run(self, run: dict) -> dict:
        """Return an evaluation sub-dict for a single run record."""
        result: dict = {
            "reward":  run.get("reward", 0.0),
            "passed":  run.get("passed", False),
            "outcome": "pass" if run.get("passed") else "fail",
        }

        # Gherkin criteria textual match (lightweight version).
        # A "passed" run that reached STOP is assumed to have met the criteria
        # since the underlying evaluator_router already awarded reward > 0.
        if self.acceptance_criteria:
            result["gherkin_criteria"] = {
                "criteria": self.acceptance_criteria,
                "met": run.get("passed", False),
            }

        return result

    # ── report persistence ────────────────────────────────────────────────────

    @staticmethod
    def save(report: dict, output_dir: str, task_id: str) -> str:
        """Serialise *report* to ``<output_dir>/<task_id>_isp_report.json``."""
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{task_id}_isp_report.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[ISP] Report saved → {path}")
        return path
