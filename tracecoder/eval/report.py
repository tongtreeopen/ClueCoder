"""Conservative aggregation of fixed evaluation attempts."""

from collections import Counter

STATUSES = {"pass", "test_fail", "agent_limit", "agent_error", "gate_error", "setup_error"}


def summarize(results: list[dict], scheduled: int) -> dict:
    if not isinstance(scheduled, int) or scheduled < 1 or len(results) > scheduled:
        raise ValueError("Invalid scheduled case count")
    cases = [dict(item) for item in results]
    for index in range(len(cases), scheduled):
        cases.append({"task_id": f"missing-{index + 1}", "status": "setup_error", "error": "Scheduled case missing"})
    for case in cases:
        if case.get("status") not in STATUSES:
            case["status"] = "setup_error"
        if case["status"] == "pass" and (case.get("gate_status") != "pass" or case.get("forbidden_paths")):
            case["status"] = "agent_error"
    reasons = set()
    if len(results) != scheduled or len({item.get("task_id") for item in cases}) != scheduled:
        reasons.add("Missing or duplicate scheduled cases")
    for case in cases:
        if not case.get("docker_valid") or not case.get("reproducible"):
            reasons.add("Docker or clean revision validation missing")
        if case.get("usage_unknown", True) or not case.get("calls", 0) or not case.get("tokens", 0):
            reasons.add("Reliable provider usage missing")
        if case["status"] in {"setup_error", "gate_error"} or case.get("gate_status") in {"setup_error", "gate_error"}:
            reasons.add("Setup or Gate execution failed")
    revisions = sorted({item["agent_revision"] for item in cases if item.get("agent_revision")})
    images = sorted({item["image_id"] for item in cases if item.get("image_id")})
    if (
        len(revisions) != 1
        or len(images) != 1
        or any(not item.get("agent_revision") or not item.get("image_id") for item in cases)
    ):
        reasons.add("Agent revision or image identity is missing or inconsistent")
    passed = sum(item["status"] == "pass" for item in cases)
    completed = sum(item.get("gate_status") in {"pass", "test_fail"} for item in cases)
    if not completed:
        reasons.add("No completed Docker Gate")
    return {
        "scheduled": scheduled,
        "passed": passed,
        "gate_completed": completed,
        "success_rate": None if reasons else passed / scheduled,
        "protocol_valid": not reasons,
        "invalid_reasons": sorted(reasons),
        "failure_counts": dict(sorted(Counter(item["status"] for item in cases if item["status"] != "pass").items())),
        "agent_revisions": revisions,
        "image_ids": images,
        "results": cases,
    }


def render_markdown(summary: dict) -> str:
    rate = summary["success_rate"]
    lines = [
        "# TraceCoder evaluation",
        "",
        f"Suite: {summary.get('suite_id', 'unknown')}",
        f"Suite SHA-256: {summary.get('suite_sha256', 'unknown')}",
        "",
        "Success rate: not measured" if rate is None else f"Success rate: {rate:.1%}",
        f"Pass / scheduled: {summary['passed']} / {summary['scheduled']}",
        f"Completed Gates: {summary['gate_completed']} / {summary['scheduled']}",
        "",
        "| Task | Status | Gate |",
        "| --- | --- | --- |",
    ]
    for case in summary["results"]:
        lines.append(f"| {case['task_id']} | {case['status']} | {case.get('gate_status') or 'not run'} |")
    lines.extend(
        [
            "",
            "Failures: " + str(summary["failure_counts"]),
            "Agent revisions: " + ", ".join(summary["agent_revisions"]),
            "Image IDs: " + ", ".join(summary["image_ids"]),
        ]
    )
    lines.extend(["", *summary["invalid_reasons"]])
    if summary.get("preflight_error"):
        lines.extend(["", summary["preflight_error"]])
    return "\n".join(lines) + "\n"
