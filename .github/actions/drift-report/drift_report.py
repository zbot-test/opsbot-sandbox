"""`opsbot/drift-report`: turns a `terraform show -json` plan and the `-detailed-exitcode` exit code
into the `opsbot/drift` check run output.

Stdlib only: this runs in whichever repo installs the action, so it can't import this workspace's packages.

The summary JSON carries only `schema_version`, `workspace`, `ran_at`, `exit_code` and `changes`
(address and action), never a resource value: plan `before`/`after` can hold secrets, so they are never read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

SCHEMA_VERSION = 1
CHECK_NAME = "opsbot/drift"

DriftAction = Literal["create", "update", "delete", "replace"]
Conclusion = Literal["success", "neutral", "failure"]
PlanOutcome = Literal["no_drift", "drift", "failed"]

_ACTION_MAP: dict[tuple[str, ...], DriftAction] = {
    ("create",): "create",
    ("update",): "update",
    ("delete",): "delete",
    ("create", "delete"): "replace",
    ("delete", "create"): "replace",
}


def drift_action(actions: list[str]) -> DriftAction | None:
    """`None` for `no-op` or `read`, which aren't drift."""
    return _ACTION_MAP.get(tuple(actions))


def changes_from_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    """Only address and action survive; `before`/`after` are never read, sensitive or not."""
    changes: list[dict[str, str]] = []
    for resource_change in plan.get("resource_changes", []):
        action = drift_action(resource_change["change"]["actions"])
        if action is not None:
            changes.append({"address": resource_change["address"], "action": action})
    return changes


def plan_outcome(exit_code: int) -> PlanOutcome:
    """`-detailed-exitcode`: 0 is no drift, 2 is drift, anything else is a failed plan."""
    if exit_code == 0:
        return "no_drift"
    if exit_code == 2:
        return "drift"
    return "failed"


_CONCLUSION: dict[PlanOutcome, Conclusion] = {"no_drift": "success", "drift": "neutral", "failed": "failure"}


def conclusion_for(exit_code: int) -> Conclusion:
    return _CONCLUSION[plan_outcome(exit_code)]


def build_report(plan: Mapping[str, Any], workspace: str, exit_code: int, ran_at: datetime) -> dict[str, Any]:
    """The `DriftReport` JSON; only a `drift` outcome reads `plan` for changes."""
    changes = changes_from_plan(plan) if plan_outcome(exit_code) == "drift" else []
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace,
        "ran_at": ran_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exit_code": exit_code,
        "changes": changes,
    }


def title_for(exit_code: int, changes: list[dict[str, str]]) -> str:
    if plan_outcome(exit_code) == "failed":
        return f"terraform plan failed (exit {exit_code})"
    if not changes:
        return "no drift"
    return f"{len(changes)} resource(s) drifted"


def check_run_output(report: dict[str, Any]) -> dict[str, str]:
    """The check run `output`; `summary` is the report JSON that `GitHubSignals.drift_signals` parses."""
    return {
        "title": title_for(report["exit_code"], report["changes"]),
        "summary": json.dumps(report, separators=(",", ":")),
    }


def load_plan(path: str | None) -> Mapping[str, Any]:
    """A missing file means the plan failed before `terraform show -json` wrote it."""
    if path is None or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as plan_file:
        return json.load(plan_file)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the opsbot/drift check run's output.")
    parser.add_argument("exit_code", type=int, help="terraform plan -detailed-exitcode's exit code")
    parser.add_argument("--plan-json", default=None, help="path to terraform show -json plan's output")
    parser.add_argument("--workspace", default="default")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    plan = load_plan(args.plan_json)
    report = build_report(plan, args.workspace, args.exit_code, datetime.now(UTC))
    output = check_run_output(report)
    conclusion = conclusion_for(args.exit_code)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as outputs:
            outputs.write(f"conclusion={conclusion}\n")
            outputs.write(f"title={output['title']}\n")
            outputs.write(f"summary<<OPSBOT_DRIFT_SUMMARY\n{output['summary']}\nOPSBOT_DRIFT_SUMMARY\n")
    else:
        print(json.dumps({"conclusion": conclusion, "output": output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
