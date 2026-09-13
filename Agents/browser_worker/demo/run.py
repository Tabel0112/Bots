"""Real local browser smoke test with an explicitly scripted reasoning substitute."""

import argparse
import asyncio
import json
import os
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import Settings
from ..runner import Worker
from ..schemas import Action, Decision, FieldSource, RecordMapping, ValueOrigin


class CatalogHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = Path(__file__).with_name("catalog.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


@contextmanager
def catalog_server(port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), CatalogHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def action(obs, **kwargs):
    values = dict(
        observation_id=obs["observation_id"],
        target_ref=None,
        semantic_target="",
        value=None,
        value_origin=None,
        url=None,
        expected_change="unchanged",
        reason="Controlled demo step.",
        records=[],
        failure_code=None,
        visual_question=None,
    )
    values.update(kwargs)
    return Action(**values)


class ScriptedCatalogReasoner:
    """Fixture policy, NEVER selected by the production API or mistaken for GPT evidence."""

    model_id = "scripted_fixture"

    def __init__(self, settings=None):
        self.extracted = False

    async def decide(self, context):
        obs, params = context["observation"], context["task"]["parameters"]
        elements = obs["elements"]
        for name, kind in [("query", "fill"), ("max_price", "select")]:
            e = next(e for e in elements if e["attributes"].get("bound_parameter") == name)
            if e["value"] != str(params[name]):
                return Decision(
                    action_type=kind,
                    arguments=action(
                        obs,
                        target_ref=e["ref"],
                        semantic_target=e["name"],
                        value=str(params[name]),
                        value_origin=ValueOrigin(kind="parameter", key=name),
                        expected_change="value_equals",
                    ),
                )
        if obs["signals"]["applied_parameters"] != {k: str(v) for k, v in params.items()}:
            e = next(
                e for e in elements if e["role"] == "button" and "click" in e["permitted_actions"]
            )
            return Decision(
                action_type="click",
                arguments=action(
                    obs,
                    target_ref=e["ref"],
                    semantic_target=e["name"],
                    expected_change="results_ready",
                ),
            )
        if not self.extracted:
            mappings = []
            for ref in obs["signals"]["record_refs"]:
                children = [e for e in elements if e["parent_ref"] == ref]
                fields = [
                    FieldSource(
                        field="title",
                        element_ref=next(e["ref"] for e in children if e["tag"] == "h3"),
                        attribute="text",
                    ),
                    FieldSource(
                        field="price",
                        element_ref=next(
                            e["ref"]
                            for e in children
                            if e["attributes"].get("data-testid") == "price"
                        ),
                        attribute="text",
                    ),
                    FieldSource(
                        field="currency",
                        element_ref=next(
                            e["ref"]
                            for e in children
                            if e["attributes"].get("data-testid") == "currency"
                        ),
                        attribute="text",
                    ),
                    FieldSource(
                        field="url",
                        element_ref=next(e["ref"] for e in children if e["role"] == "link"),
                        attribute="href",
                    ),
                ]
                mappings.append(RecordMapping(container_ref=ref, fields=fields))
            self.extracted = True
            return Decision(action_type="extract_records", arguments=action(obs, records=mappings))
        return Decision(action_type="report_success", arguments=action(obs))


def sample_request():
    return json.loads((Path(__file__).parents[1] / "examples/subtask.json").read_text("utf-8"))


def main():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-only", action="store_true")
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="Use GPT-5.4 instead of the scripted fixture policy",
    )
    parser.add_argument("--executable", default=os.getenv("WORKER_BROWSER_EXECUTABLE"))
    args = parser.parse_args()
    with catalog_server():
        if args.serve_only:
            print("Controlled catalog: http://127.0.0.1:8765 (Ctrl+C to stop)", flush=True)
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                return
        else:
            settings = Settings.from_env().model_copy(
                update={"browser": "local", "browser_executable": args.executable}
            )
            worker = Worker(
                settings=settings,
                reasoner_factory=None if args.live_model else ScriptedCatalogReasoner,
            )
            report = asyncio.run(worker.run(sample_request()))
            print(
                json.dumps(
                    {
                        "reasoning": "gpt-5.4" if args.live_model else "scripted_fixture",
                        "browser": "real_local_chromium",
                        "report": report.model_dump(mode="json"),
                    },
                    indent=2,
                )
            )
            raise SystemExit(0 if report.outcome == "succeeded" else 1)


if __name__ == "__main__":
    main()
