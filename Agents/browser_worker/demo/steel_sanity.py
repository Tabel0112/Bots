"""Credentialed Steel lifecycle sanity check without an OpenAI model call."""

import argparse
import asyncio
import getpass
import json
import os
import time

from ..browser.adapter import BrowserAdapter
from ..config import Settings, SiteConfig
from ..policy import validate_request
from ..schemas import SubtaskRequest, WorkerError


def make_site() -> SiteConfig:
    return SiteConfig(
        site_id="steel-sanity-example",
        start_url="https://example.com/",
        allowed_domains=["example.com"],
        allowed_url_patterns=["https://example.com/"],
        controls=[],
        parameters_schema={"type": "object", "additionalProperties": False},
        output_schema_id="steel-sanity.v1",
        record_schema={"type": "object", "additionalProperties": False},
        results_selector="[data-steel-sanity-results]",
        record_selector="[data-steel-sanity-record]",
        empty_selector="[data-steel-sanity-empty]",
        parameter_evidence=[],
    )


def make_request() -> SubtaskRequest:
    return SubtaskRequest.model_validate(
        {
            "schema_version": "0.2",
            "request_id": "steel-sanity-request",
            "run_id": "steel-sanity-run",
            "subtask_id": "steel-sanity-navigation",
            "objective": "Open the configured example page and observe its DOM.",
            "site_id": "steel-sanity-example",
            "operation": "search_extract",
            "session": {"ownership": "worker"},
            "parameters": {},
            "output_schema_id": "steel-sanity.v1",
            "success_conditions": [],
            "allowed_actions": ["navigate"],
            "allowed_domains": ["example.com"],
            "allowed_url_patterns": ["https://example.com/"],
            "budgets": {
                "max_actions": 1,
                "max_model_calls": 1,
                "max_retries": 1,
                "max_runtime_seconds": 60,
                "no_progress_limit": 2,
            },
            "required_evidence": ["observations"],
            "prerequisites": [],
            "visual_fallback_available": False,
        }
    )


async def run(api_key: str) -> tuple[dict, int]:
    os.environ["STEEL_API_KEY"] = api_key
    site = make_site()
    task, _ = validate_request(make_request(), {site.site_id: site})
    settings = Settings(browser="steel", browser_timeout_seconds=15)
    adapter = BrowserAdapter(task, site, settings)
    started = time.monotonic()
    result = {
        "check": "steel_create_connect_navigate_observe_release",
        "target": "https://example.com/",
        "session_created": False,
        "connected": False,
        "observed": False,
        "title": None,
        "final_url": None,
        "visible_element_count": 0,
        "session_disposition": "not_started",
        "elapsed_ms": 0,
        "outcome": "failed",
        "error": None,
    }
    exit_code = 1
    try:
        await adapter.start()
        result["session_created"] = bool(adapter.session_ref)
        result["connected"] = adapter.page is not None
        # The adapter no longer needs to read the key after it has created and
        # connected the clients; remove the process environment copy promptly.
        os.environ.pop("STEEL_API_KEY", None)
        observation = await adapter.observe("steel-sanity-execution")
        result.update(
            {
                "observed": True,
                "title": observation.title,
                "final_url": observation.url,
                "visible_element_count": len(observation.elements),
            }
        )
        if observation.url == "https://example.com/" and observation.title == "Example Domain":
            result["outcome"] = "succeeded"
            exit_code = 0
        else:
            result["error"] = {
                "code": "OBSERVATION_MISMATCH",
                "message": "Steel connected, but the expected example page was not observed.",
            }
    except WorkerError as exc:
        result["error"] = exc.failure.model_dump(mode="json")
    except Exception:
        # Provider exceptions may include a credential-bearing CDP URL.
        result["error"] = {
            "code": "INTERNAL_ERROR",
            "message": "The Steel sanity check failed before a safe detailed result was available.",
        }
    finally:
        os.environ.pop("STEEL_API_KEY", None)
        try:
            result["session_disposition"] = await asyncio.wait_for(adapter.close(), timeout=20)
        except Exception:
            result["session_disposition"] = "cleanup_failed"
        if result["session_disposition"] != "released":
            result["outcome"] = "failed"
            exit_code = 1
            result["error"] = result["error"] or {
                "code": "CLEANUP_FAILED",
                "message": "The worker-owned Steel session was not confirmed released.",
            }
        result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result, exit_code


async def run_sdk_only(api_key: str) -> tuple[dict, int]:
    """Diagnose credential/session creation without printing provider error bodies."""
    from steel import AsyncSteel

    client = AsyncSteel(steel_api_key=api_key, max_retries=0, timeout=15)
    session = None
    result = {
        "check": "steel_sdk_create_release",
        "session_created": False,
        "session_disposition": "not_started",
        "outcome": "failed",
        "provider_error_type": None,
        "provider_status_code": None,
    }
    try:
        session = await client.sessions.create(api_timeout=60000)
        result["session_created"] = True
        result["outcome"] = "succeeded"
    except Exception as exc:
        result["provider_error_type"] = type(exc).__name__
        status = getattr(exc, "status_code", None)
        result["provider_status_code"] = status if isinstance(status, int) else None
    finally:
        if session is not None:
            try:
                await client.sessions.release(session.id)
                result["session_disposition"] = "released"
            except Exception as exc:
                result["outcome"] = "failed"
                result["session_disposition"] = "cleanup_failed"
                result["provider_error_type"] = type(exc).__name__
                status = getattr(exc, "status_code", None)
                result["provider_status_code"] = status if isinstance(status, int) else None
        await client.close()
    return result, 0 if result["outcome"] == "succeeded" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-environment",
        action="store_true",
        help="Read STEEL_API_KEY from the environment instead of a hidden prompt.",
    )
    parser.add_argument(
        "--sdk-only",
        action="store_true",
        help="Only create and release a Steel SDK session; report safe status metadata.",
    )
    args = parser.parse_args()
    api_key = (
        os.getenv("STEEL_API_KEY")
        if args.use_environment
        else getpass.getpass("Steel API key (hidden): ")
    )
    if not api_key:
        raise SystemExit("No Steel API key was supplied.")
    result, exit_code = asyncio.run(run_sdk_only(api_key) if args.sdk_only else run(api_key))
    print(json.dumps(result, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
