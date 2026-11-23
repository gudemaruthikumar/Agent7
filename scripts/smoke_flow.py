"""Manual smoke check: walk one change through the full revalidation flow."""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body or {}).encode() if method != "GET" else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def main() -> None:
    call("/v1/kill-switch", "PUT", {"enabled": False})
    changes = call("/v1/changes")
    print("changes:", [(item["change_id"], item["status"]) for item in changes])
    pending = next(item for item in changes if item["status"] in {"detected", "impact_ready"})
    ran = call(f"/v1/changes/{pending['change_id']}/run", "POST")
    print("status:", ran["status"], "tests:", ran["selected_test_ids"])
    print("posture:", ran["posture_before"], "->", ran["posture_after"])
    for row in ran["comparisons"]:
        print(
            "  ",
            row["metric_id"],
            row["previous"],
            "->",
            row["actual"],
            "pass" if row["passed"] else "fail",
        )
    decided = call(
        f"/v1/changes/{pending['change_id']}/approve",
        "POST",
        {"reviewer": "Product Owner", "notes": "Reviewed in smoke check."},
    )
    print("decision:", decided["status"], decided["posture_after"])
    print("dashboard:", call("/v1/dashboard"))


if __name__ == "__main__":
    main()
