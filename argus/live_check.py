"""Live interpretation check: run real requests through interpret() and gate().

Usage (from the repository root, with OPENAI_API_KEY and ARGUS_MODEL in .env or the
environment; OPENAI_BASE_URL optional):

    python3 -m argus.live_check                # built-in sample requests
    python3 -m argus.live_check "text" "text"  # your own requests
    python3 -m argus.live_check --model gpt-5.4 --compare gpt-5.6-sol

Prints, per request: the model used, each intent (kind, site or domain, operation,
parameters with their spans and confidence, criteria), missing parameters,
ambiguities, and the gate decision with its rule.  Nothing is stored and no browser
runs.  The API key is read from the environment or .env and never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from argus.contracts import ContractError
from argus.gate import gate
from argus.interpreter import interpret
from argus.model_client import OpenAICompatibleClient

SAMPLES = [
    "Find headphones under $150 in the demo catalog",
    "find me headphones under 150 and speakers under 200 on the demo catalog",
    "Show me the five cheapest keyboards",
    "Find something nice for my desk",
    "Find the best 10 software engineering jobs on jobs.example.com",
    "Find the highest salary 10 software engineering jobs, remote only, on https://jobs.example.com/search",
    "What is the top story on news.ycombinator.com right now and how many points does it have",
    "Compare laptop prices across amazon.com and bestbuy.com",
    "Log in to my bank and download my statements",
    "How do I log in to GitHub with two-factor authentication? Look at docs.github.com",
    "List the ten most recent arXiv papers about protein folding on arxiv.org",
    "Book me a table for two tonight",
]


def load_env(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value and key not in os.environ:
            os.environ[key] = value


def describe(text: str, request_id: str, model: str | None) -> None:
    client = OpenAICompatibleClient(model=model)
    started = time.monotonic()
    try:
        interpreted = interpret(text, request_id, client=client)
    except ContractError as exc:
        print(f"  ERROR {exc.typed_error.code}: {exc}")
        return
    elapsed = time.monotonic() - started
    print(f"  model={interpreted.model}  {elapsed:.1f}s")
    for index, intent in enumerate(interpreted.intents):
        where = intent.target_domain if intent.kind == "open" else intent.site_id
        print(
            f"  intent {index}: kind={intent.kind} where={where!r} operation={intent.operation!r} confidence={intent.confidence:.2f}"
        )
        if intent.goal:
            print(f"    goal: {intent.goal}")
        for name, origin in intent.parameters.items():
            quoted = text[origin.span[0] : origin.span[1]] if origin.span else None
            print(
                f"    param {name}={origin.value!r} source={origin.source} conf={origin.confidence:.2f} span={origin.span} quoted={quoted!r}"
            )
        for criterion in intent.criteria:
            quoted = (
                text[criterion.span[0] : criterion.span[1]] if criterion.span else None
            )
            print(
                f"    criterion {criterion.kind} text={criterion.text!r} parameter={criterion.parameter!r} conf={criterion.confidence:.2f} quoted={quoted!r}"
            )
        if intent.expected_record_shape:
            print(f"    record shape: {intent.expected_record_shape}")
    for missing in interpreted.missing_required:
        print(
            f"  missing: intent {missing.intent_index} {missing.parameter}: {missing.question}"
        )
    for ambiguity in interpreted.ambiguities:
        print(f"  ambiguity: {ambiguity}")
    decision = gate(interpreted)
    print(f"  GATE {decision.decision} [{decision.rule_id}] {decision.reason}")
    for question in decision.questions:
        print(f"    question: {question}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "requests", nargs="*", help="request texts; defaults to the built-in samples"
    )
    parser.add_argument("--model", help="override ARGUS_MODEL")
    parser.add_argument(
        "--compare", help="also run every request with this second model"
    )
    args = parser.parse_args(argv)
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set (environment or .env)", file=sys.stderr)
        return 2
    requests = args.requests or SAMPLES
    models = [args.model] + ([args.compare] if args.compare else [])
    for number, text in enumerate(requests, 1):
        print(f"\n[{number}] {text}")
        for model in models:
            if len(models) > 1:
                print(f" -- {model or os.environ.get('ARGUS_MODEL')}")
            describe(text, f"live-{number}", model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
