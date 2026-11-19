"""`opsbot/drift-report` (design-aws.md §5.5, plan.md WP-70): turns a `terraform show -json`
plan plus the exit code `terraform plan -detailed-exitcode` returned into the `opsbot/drift`
check run's output.

Stdlib only, no `opsbot_github` import. This script runs in whichever repo installs the
action, not this one, so it can't depend on this workspace's packages; `action.yml` next to
it is a composite action so it carries no build step either. Candidate for a split into its
own `opsbot/drift-report` repo later (plan.md WP-70 ticket notes); nothing here reaches back
into this workspace, so the split is a copy, not a rewrite.

`opsbot_github.pure.signals.DriftReport` is the contract this builds against
(`tests/drift_report/test_drift_report.py` feeds this script's own output through that
parser). The summary JSON must carry only `schema_version`, `workspace`, `ran_at`,
`exit_code` and `changes` (each an address and one of create/update/delete/replace), and
never a resource value: plan output can carry secrets in `before`/`after`, which is exactly
why `before_sensitive`/`after_sensitive` and the `before`/`after` payloads themselves are
never read below.
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
    """One Terraform resource change's `change.actions`; `None` for `no-op` or `read`, which
    aren't drift."""
    return _ACTION_MAP.get(tuple(actions))


def changes_from_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    """Only the address and action survive (design-aws.md §5.5): `change.before`, `after`
    and the `*_sensitive` maps are never read, so a secret resource value can't reach the
    result regardless of whether Terraform flagged it sensitive."""
    changes: list[dict[str, str]] = []
    for resource_change in plan.get("resource_changes", []):
        action = drift_action(resource_change["change"]["actions"])
        if action is not None:
            changes.append({"address": resource_change["address"], "action": action})
    return changes


def plan_outcome(exit_code: int) -> PlanOutcome:
    """design-aws.md §5.5 and `terraform plan -detailed-exitcode`'s own contract: 0 is no
    drift, 2 is drift. Any other code means the plan itself failed (plan.md WP-70: "treat
    any other code as a failed run"). The one place that decides what an exit code means;
    `build_report`, `title_for` and `conclusion_for` all read this instead of each
    re-deriving it from `exit_code`."""
    if exit_code == 0:
        return "no_drift"
    if exit_code == 2:
        return "drift"
    return "failed"


_CONCLUSION: dict[PlanOutcome, Conclusion] = {"no_drift": "success", "drift": "neutral", "failed": "failure"}


def conclusion_for(exit_code: int) -> Conclusion:
    return _CONCLUSION[plan_outcome(exit_code)]


def build_report(plan: Mapping[str, Any], workspace: str, exit_code: int, ran_at: datetime) -> dict[str, Any]:
    """The verbatim `DriftReport` JSON (plan.md WP-70, `opsbot_github.pure.signals`).
    Only a `drift` outcome reads `plan` for changes: `no_drift` is terraform's own signal,
    trusted over re-deriving it, and `failed` means the plan errored, so `changes` is `[]`
    there too rather than whatever happens to be in `plan`."""
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
    """The GitHub check run `output` object: free-text `title`, and `summary` as the
    `DriftReport` JSON verbatim, the shape `GitHubSignals.drift_signals` parses."""
    return {
        "title": title_for(report["exit_code"], report["changes"]),
        "summary": json.dumps(report, separators=(",", ":")),
    }


def load_plan(path: str | None) -> Mapping[str, Any]:
    """`None`, or a path `terraform show -json` never wrote because the plan itself failed,
    both mean "nothing to read"."""
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
