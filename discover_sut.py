import argparse
import importlib.util
import json
import logging
import sys

from AgentOccam.logger import logger

REQUIRED_MODULES = {
    "yaml": "PyYAML",
    "gymnasium": "gymnasium",
    "playwright": "playwright",
    "PIL": "Pillow",
}


def get_missing_dependencies() -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for module_name, package_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append({"module": module_name, "package": package_name})
    return missing


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(level)
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(level)


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover SUT screens and generate draft tasks.")
    parser.add_argument("--config", type=str, help="Path to discovery YAML config.")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level for discovery progress output.",
    )
    parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check whether the local Python environment has the required discovery dependencies.",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)
    logger = logging.getLogger("discover_sut")

    missing = get_missing_dependencies()
    if args.check_deps:
        logger.info("Checking discovery runtime dependencies.")
        print(json.dumps({"missing_dependencies": missing}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if not missing else 1)
    if not args.config:
        parser.error("--config is required unless --check-deps is used.")
    if missing:
        print(
            json.dumps(
                {
                    "error": "Missing discovery runtime dependencies.",
                    "missing_dependencies": missing,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)

    from AgentOccam.discovery.runtime import run_discovery_from_config_path

    logger.info("Starting discovery run with config: %s", args.config)
    summary = run_discovery_from_config_path(args.config)
    logger.info(
        "Discovery finished. screens=%s transitions=%s tasks=%s run_dir=%s",
        summary.get("num_screens"),
        summary.get("num_transitions"),
        summary.get("num_generated_tasks"),
        summary.get("run_dir"),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
