from __future__ import annotations

import argparse
import json
import sys

from rmte_spine.db import create_schema, session_scope
from rmte_spine.ingest import IngestError, ingest_tlai
from rmte_spine.library import export_libraries
from rmte_spine.query import (
    SpineError,
    get_metric,
    get_risk,
    list_releases,
    rmte_links,
)


def _print(obj) -> None:
    json.dump(obj, sys.stdout, indent=2, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rmte-spine",
        description="TrustLensAI RMTE spine — ingest TLAI workbooks and query every archetype.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ingest = sub.add_parser("ingest", help="Publish every TLAI Logs/Layers/Formulas workbook")
    ingest.add_argument("--replace", action="store_true")

    sub.add_parser(
        "export-libraries",
        help="Write Agent-5-shaped twelve-file libraries under libraries/<archetype>/v1",
    )

    sub.add_parser("list", help="List published archetype releases")

    metric = sub.add_parser("metric", help="Show one metric with risks and formula terms")
    metric.add_argument("archetype")
    metric.add_argument("metric_id")

    risk = sub.add_parser("risk", help="Show one risk and linked metrics")
    risk.add_argument("archetype")
    risk.add_argument("risk_id")

    spine = sub.add_parser("spine", help="Show RMTE master rows (Risk → Metric → Test → Evidence)")
    spine.add_argument("archetype")
    spine.add_argument("--metric")
    spine.add_argument("--risk")

    args = parser.parse_args(argv)
    create_schema()
    try:
        with session_scope() as session:
            if args.cmd == "ingest":
                rows = ingest_tlai(session, replace=args.replace)
                _print(rows)
                return 0
            if args.cmd == "export-libraries":
                _print(export_libraries())
                return 0
            if args.cmd == "list":
                _print(list_releases(session))
                return 0
            if args.cmd == "metric":
                _print(get_metric(session, args.archetype, args.metric_id))
                return 0
            if args.cmd == "risk":
                _print(get_risk(session, args.archetype, args.risk_id))
                return 0
            if args.cmd == "spine":
                _print(
                    rmte_links(
                        session,
                        args.archetype,
                        metric_id=args.metric,
                        risk_id=args.risk,
                    )
                )
                return 0
    except (IngestError, SpineError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
