"""Command line entry point: ``python -m argus "<request text>"``.

One request, one run, one JSON result on stdout.  This is the phase 2 wiring of
docs/hackathon/ARGUS-IMPLEMENTATION.md: the controller is real, everything it
calls out through is a fake.

.. code-block:: text

    python -m argus "Find headphones under $150 in the demo catalog"
    python -m argus --interpreted argus/examples/interpreted_request.json \
        --store /tmp/argus-demo

``--fake`` is the default and wires :class:`~argus.fakes.FakeToolbox`,
:class:`~argus.fakes.StubModerator`, :class:`~argus.fakes.FakeGhost` and
:class:`~argus.store.JsonStore`.  ``--no-fake`` exits with a message rather than
pretending: no real toolbox, moderator or Ghost is connected yet (phase 3).

``--interpreted FILE`` skips stage 1 and reads an
:class:`~argus.contracts.InterpretedRequest` from JSON, so the whole path from
gate to published result runs offline.  Without it, stage 1 calls the model and
needs the ``anthropic`` SDK installed and ``ANTHROPIC_API_KEY`` set; ``ARGUS_MODEL``
overrides the model.  A missing SDK is not a crash: the run ends as a normal
failed result carrying ``PRECONDITION_FAILED``.

Output: the terminal :class:`~argus.contracts.RunResult` as JSON on stdout,
nothing else, so it can be piped.  Progress notes and the store location go to
stderr.  Exit status is 0 when the run succeeded, 1 for any other terminal
status (``failed``, ``cancelled``, ``needs_input``), 2 for a usage error and 3
when a real toolbox was asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

from argus.contracts import ContractError, InterpretedRequest, RunResult
from argus.controller import Controller
from argus.fakes import FakeGhost, FakeToolbox, StubModerator
from argus.store import JsonStore

__all__ = ["DEFAULT_STORE_DIR", "EXIT_OK", "EXIT_RUN_NOT_SUCCEEDED", "EXIT_NO_TOOLBOX",
           "build_parser", "main"]

#: Where runs are written when ``--store`` is not given.
DEFAULT_STORE_DIR = "argus-runs"

EXIT_OK = 0
EXIT_RUN_NOT_SUCCEEDED = 1
EXIT_USAGE = 2
EXIT_NO_TOOLBOX = 3

_NO_TOOLBOX_MESSAGE = (
    "No real toolbox, moderator or Ghost is connected yet, so there is nothing "
    "to run without --fake.\n"
    "  toolbox   Thomas and Tianqi (Steel sessions, dom_interpret, vision_interpret)\n"
    "  moderator Thomas (assess_report, reconcile, synthesize)\n"
    "  ghost     Sting (match, validate, compile)\n"
    "Connecting them is phase 3 of docs/hackathon/ARGUS-IMPLEMENTATION.md.\n"
    "Run the same request against the fakes with --fake (the default)."
)


def build_parser() -> argparse.ArgumentParser:
    """The command line, exposed so the tests and the README stay in step."""
    parser = argparse.ArgumentParser(
        prog="python -m argus",
        description=(
            "Run one request through the ARGUS controller and print the terminal "
            "result as JSON."
        ),
        epilog=(
            "Interpretation calls the model unless --interpreted supplies an "
            "already-interpreted request."
        ),
    )
    parser.add_argument(
        "text",
        nargs="?",
        help="the request in plain language; omit it when --interpreted is given",
    )
    parser.add_argument(
        "--fake",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="run against the fakes (default); --no-fake asks for a real toolbox",
    )
    parser.add_argument(
        "--store",
        default=DEFAULT_STORE_DIR,
        metavar="DIR",
        help=f"directory for runs, events, reports and skills (default: {DEFAULT_STORE_DIR})",
    )
    parser.add_argument(
        "--request-id",
        metavar="ID",
        help="request ID to record (default: the interpreted request's, else a fresh one)",
    )
    parser.add_argument(
        "--interpreted",
        metavar="FILE",
        help="JSON InterpretedRequest to run instead of calling the model",
    )
    return parser


def _load_interpreted(path: str) -> InterpretedRequest:
    """Read an InterpretedRequest fixture, failing with a usage-level message."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read --interpreted {path}: {exc.strerror or exc}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--interpreted {path} is not valid JSON: {exc}")
    try:
        return InterpretedRequest.from_dict(data)
    except ContractError as exc:
        raise SystemExit(f"--interpreted {path} is not an InterpretedRequest: {exc}")


def _describe(result: RunResult, store: JsonStore) -> str:
    """One human line for stderr; the machine-readable answer is the JSON."""
    parts = [f"run {result.run_id}: {result.status}"]
    if result.error is not None:
        parts.append(f"{result.error.code}: {result.error.message}")
    parts.append(f"stored in {store.run_dir(result.run_id)}")
    return " | ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit status."""
    args = build_parser().parse_args(argv)

    if not args.fake:
        print(_NO_TOOLBOX_MESSAGE, file=sys.stderr)
        return EXIT_NO_TOOLBOX

    if args.interpreted and args.text:
        print(
            "pass either the request text or --interpreted FILE, not both: the "
            "fixture already carries the text it was interpreted from.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not args.interpreted and not args.text:
        print(
            "nothing to run: give the request text, or --interpreted FILE to run "
            "an already-interpreted request offline.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    request: Any
    if args.interpreted:
        request = _load_interpreted(args.interpreted)
        request_id = args.request_id or request.request_id
    else:
        request = args.text
        request_id = args.request_id or f"request-{uuid.uuid4().hex[:8]}"

    store = JsonStore(args.store)
    controller = Controller(
        toolbox=FakeToolbox(),
        moderator=StubModerator(),
        ghost=FakeGhost(),
        store=store,
    )
    result = controller.run(request, request_id)

    json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    print(_describe(result, store), file=sys.stderr)
    return EXIT_OK if result.status == "succeeded" else EXIT_RUN_NOT_SUCCEEDED


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
