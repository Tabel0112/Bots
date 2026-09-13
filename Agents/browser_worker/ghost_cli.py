"""Run the worker–Ghost connection or explicitly qualify a candidate."""

import argparse
import asyncio
import ipaddress
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from ghostapi.client import GhostClient

from .config import Settings, load_sites
from .demo.run import ScriptedCatalogReasoner, catalog_server, sample_request
from .ghost import GhostWorkflow
from .runner import Worker


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", type=Path, help="SubtaskRequest 0.2 JSON file")
    source.add_argument(
        "--demo",
        action="store_true",
        help="Local Chrome catalog with scripted exploration; no paid services",
    )
    parser.add_argument(
        "--live-steel",
        action="store_true",
        help="Run the request through hosted Steel and GPT (requires --request)",
    )
    parser.add_argument(
        "--quiet-status",
        action="store_true",
        help="Suppress live Steel progress events on stderr",
    )
    parser.add_argument("--ghost-url", default=os.getenv("GHOST_API_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--qualify", nargs=2, metavar=("SKILL_ID", "VERSION"))
    parser.add_argument("--inputs", type=Path, help="Three qualification parameter sets as JSON")
    parser.add_argument("--query", default="headphones", help="Query for the local demo")
    parser.add_argument("--max-price", type=int, default=150, help="Price bound for the local demo")
    args = parser.parse_args()
    if args.qualify and not args.inputs:
        parser.error("--qualify requires --inputs")
    if args.live_steel and (args.demo or not args.request):
        parser.error("--live-steel requires --request and cannot be combined with --demo")
    if args.request and not args.live_steel:
        parser.error("--request requires --live-steel; use --demo for the local fixture")
    try:
        sites = load_sites()
    except Exception as exc:
        parser.error(f"could not load site configuration: {exc}")
    try:
        settings = Settings.from_env()
    except Exception:
        if args.live_steel:
            parser.error(
                "live Steel settings are invalid; use OPENAI_MODEL=gpt-5.4 and "
                "OPENAI_REASONING_EFFORT=medium or high"
            )
        raise
    try:
        raw = json.loads(args.request.read_text()) if args.request else sample_request()
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"could not read request JSON: {exc}")
    if args.live_steel:
        if not os.getenv("STEEL_API_KEY"):
            parser.error("--live-steel requires STEEL_API_KEY in the environment or .env")
        if not os.getenv("OPENAI_API_KEY"):
            parser.error("--live-steel requires OPENAI_API_KEY in the environment or .env")
        if settings.browser != "steel":
            parser.error("--live-steel requires WORKER_BROWSER=steel")
        if not args.request:
            parser.error("--live-steel requires --request with a hosted site task")
        parsed = urlparse(raw.get("start_url") or "")
        hostname = parsed.hostname
        try:
            address = ipaddress.ip_address(hostname) if hostname else None
        except ValueError:
            address = None
        if (
            parsed.scheme != "https"
            or not hostname
            or hostname in {"localhost"}
            or (address and (address.is_private or address.is_loopback or address.is_link_local))
        ):
            parser.error(
                "--live-steel requires an HTTPS hosted start_url; private or loopback "
                "targets are not allowed"
            )
    if args.demo:
        executable = os.getenv("WORKER_BROWSER_EXECUTABLE")
        if (
            not executable
            and Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists()
        ):
            executable = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        settings = Settings(browser="local", browser_executable=executable)
        site = sites["demo-catalog"]
        sites["demo-catalog"] = site.model_copy(
            update={
                "start_url": site.start_url.replace(":8765", ":8766"),
                "allowed_url_patterns": [
                    p.replace(":8765", ":8766") for p in site.allowed_url_patterns
                ],
            }
        )
        raw["start_url"] = raw["start_url"].replace(":8765", ":8766")
        raw["allowed_url_patterns"] = [
            p.replace(":8765", ":8766") for p in raw["allowed_url_patterns"]
        ]
        raw["parameters"] = {"query": args.query, "max_price": args.max_price}
        raw["objective"] = (
            f"Search the catalog for {args.query} costing at most {args.max_price} USD and return visible products."
        )
        raw["visual_fallback_available"] = False
    worker = Worker(
        sites=sites,
        settings=settings,
        reasoner_factory=ScriptedCatalogReasoner if args.demo else None,
    )

    def on_status(stage):
        if not args.quiet_status and args.live_steel:
            print(f"[live-steel] {stage}", file=sys.stderr, flush=True)

    async def execute():
        async with GhostClient(args.ghost_url) as client:
            connection = GhostWorkflow(worker, client)
            if args.qualify:
                result = await connection.qualify(
                    raw, args.qualify[0], int(args.qualify[1]), json.loads(args.inputs.read_text())
                )
                return {
                    "qualification": result["qualification"],
                    "runs": [
                        {"outcome": r["outcome"], "records": r["records"], "metrics": r["metrics"]}
                        for r in result["runs"]
                    ],
                }
            report = await connection.run(raw, on_status=on_status)
            return {
                "outcome": report.outcome,
                "summary": report.summary,
                "records": [r.data for r in report.records],
                "ghost": report.ghost,
                "metrics": report.metrics.model_dump(),
                "session_disposition": report.session_disposition,
                "limitations": report.limitations,
                "visual_report": report.visual_report,
            }

    with catalog_server(port=8766) if args.demo else nullcontext():
        result = asyncio.run(execute())
        print(json.dumps(result, indent=2))
        success = (
            result.get("outcome") == "succeeded"
            or result.get("qualification", {}).get("status") == "qualified"
        )
        raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
