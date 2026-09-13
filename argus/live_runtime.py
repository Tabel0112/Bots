"""Live Steel toolbox and validation bridge for Mission Control."""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus, urlsplit
from uuid import uuid4

from argus.contracts import TypedError, WorkerReport
from argus.controller import Controller
from argus.demo_runtime import DemoModerator, demo_plan
from argus.fakes import FakeToolbox
from argus.store import JsonStore
from ghostapi.api.models import LookupRequest
from ghostapi.api.service import lookup as ghost_lookup
from ghostapi.api.storage import WorkflowStore


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class LiveSteelToolbox(FakeToolbox):
    """ARGUS Toolbox that visits real public pages in worker-owned Steel sessions."""

    def __init__(self):
        super().__init__()
        self._virtual = 0

    def open_session(self, site_id: str) -> str:
        with self._lock:
            self._virtual += 1
            handle = f"steel-task-{self._virtual}"
            self.sessions[handle] = True
            self.calls.append(("open_session", site_id, handle))
        return handle

    def run_subtask(self, subtask_input):
        with self._lock:
            self.calls.append(
                (
                    "run_subtask",
                    subtask_input.subtask.subtask_id,
                    "live",
                    "steel",
                    subtask_input.session_handle,
                )
            )
        try:
            return asyncio.run(self._browse(subtask_input))
        except Exception as exc:  # noqa: BLE001 - any browser failure becomes a typed failed report
            return self._failed_report(subtask_input, type(exc).__name__)

    async def _browse(self, subtask_input):
        import os

        from playwright.async_api import async_playwright
        from steel import AsyncSteel

        task = subtask_input.subtask
        key = os.getenv("STEEL_API_KEY")
        if not key:
            raise RuntimeError("STEEL_API_KEY is missing")
        client = AsyncSteel(steel_api_key=key, max_retries=0, timeout=20)
        session = browser = playwright = None
        started = time.monotonic()
        try:
            session = await client.sessions.create(api_timeout=120000)
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(
                f"wss://connect.steel.dev?apiKey={key}&sessionId={session.id}",
                timeout=20000,
            )
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(20000)
            target = self._target(task)
            target_host = urlsplit(target).hostname

            async def guard(route):
                request = route.request
                host = urlsplit(request.url).hostname
                if request.method not in {"GET", "HEAD"} or (
                    request.is_navigation_request() and host != target_host
                ):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            await page.goto(target, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
            observation = f"steel-{session.id[:8]}-{uuid4().hex[:6]}"
            if task.subtask_id.startswith("shopping"):
                records = await self._shopping(page, task, observation)
            elif task.subtask_id.startswith("travel"):
                records = await self._travel(page, task, observation)
            else:
                records = await self._jobs(page, task, observation)
            title = await page.title()
            action = {
                "step_id": "steel-navigation",
                "action": {"name": "open_url", "input": {"url": target}},
                "semantic_target": title,
                "observation_before": None,
                "observation_after": observation,
                "url": page.url,
                "outcome": "succeeded",
                "timestamp": time.time(),
                "worker_reasoning": None,
            }
            extract = {
                "step_id": "steel-extraction",
                "action": {"name": "extract", "input": {"records": len(records)}},
                "semantic_target": "visible result records",
                "observation_before": observation,
                "observation_after": observation,
                "url": page.url,
                "outcome": "succeeded",
                "timestamp": time.time(),
                "worker_reasoning": None,
            }
            return WorkerReport(
                "0.5-live",
                "steel-dom",
                "deterministic-dom",
                subtask_input.request_id or subtask_input.run_id,
                task.subtask_id,
                task.goal or task.operation,
                "succeeded",
                f"Steel observed {title!r} and extracted {len(records)} current records.",
                records,
                [action, extract],
                {
                    "screenshots": [observation],
                    "source_url": page.url,
                    "page_title": title,
                },
                {
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "browser_action_count": 2,
                    "model_call_count": 0,
                },
                [],
                subtask_input.session_handle,
                [],
            )
        finally:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
            if session:
                await client.sessions.release(session.id)
            await client.close()

    @staticmethod
    def _target(task):
        params = task.parameters or {}
        if task.subtask_id.startswith("shopping"):
            query = str(params.get("query") or "headphones").replace(
                "keyboard", "keyboards"
            )
            return f"https://www.staples.com/{quote_plus(query)}/directory_{quote_plus(query)}"
        if task.subtask_id.startswith("travel"):
            return "https://en.wikivoyage.org/wiki/Toronto"
        return "https://remotive.com/remote-jobs/software-development"

    @staticmethod
    async def _shopping(page, task, observation):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, 0)")
        text = await page.locator("body").inner_text()
        pattern = re.compile(
            r"Item\s*:?\s*(\S+).*?Model\s*:?\s*(\S+).*?Price is\s*\$([0-9,]+(?:\.[0-9]{2})?)",
            re.DOTALL | re.IGNORECASE,
        )
        maximum = float((task.parameters or {}).get("max_price") or 10**9)
        category = str((task.parameters or {}).get("query") or "product")
        records = []
        for item, model, price_text in pattern.findall(text):
            price = float(price_text.replace(",", ""))
            if price <= maximum:
                records.append(
                    {
                        "title": f"{category.title()} {model}",
                        "item": item,
                        "price": price,
                        "currency": "USD",
                        "category": category.rstrip("s") + "s",
                        "url": page.url,
                        "source_observation_id": observation,
                        "retrieved_at": _now(),
                    }
                )
            if len(records) >= 5:
                break
        return records

    @staticmethod
    async def _travel(page, task, observation):
        listings = await page.locator(".vcard").evaluate_all("""els => {
            const headings = [...document.querySelectorAll('h2')];
            return els.map(el => ({
                text: (el.innerText || '').replace(/\\s+/g, ' ').trim(),
                section: (headings.filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).at(-1)?.innerText || '').trim(),
                listingUrl: el.querySelector('a[href]')?.href || ''
            })).filter(x => ['See', 'Do', 'Eat'].some(name => x.section.startsWith(name)) && x.text.length > 12);
        }""")
        chosen = []
        for section in ("Eat", "See", "Do"):
            chosen.extend(
                item for item in listings if item["section"].startswith(section)
            )
        seen, records = set(), []
        for listing in chosen:
            title = re.sub(r"^\d+\s+", "", listing["text"].split(".", 1)[0]).strip()
            if not title or title in seen:
                continue
            seen.add(title)
            index = len(records)
            records.append(
                {
                    "title": title[:140],
                    "day": index // 2 + 1,
                    "time": "Morning" if index % 2 == 0 else "Afternoon",
                    "kind": listing["section"].split()[0].lower(),
                    "area": "Toronto",
                    "url": f"{page.url}#{quote_plus(title)}",
                    "listing_url": listing["listingUrl"],
                    "source_observation_id": observation,
                }
            )
            if len(records) >= 6:
                break
        return records

    @staticmethod
    async def _jobs(page, task, observation):
        links = await page.locator(
            "a[href*='/remote-jobs/software-development/']"
        ).evaluate_all(
            "els => els.map(a => ({title:(a.textContent||'').trim(), href:a.href, card:(a.parentElement?.parentElement?.innerText||'')})).filter(x => x.title.length > 8 && x.title !== 'View Job >')"
        )
        seen, records = set(), []
        for link in links:
            if link["href"] in seen:
                continue
            salary = re.search(
                r"\$([0-9,.]+)\s*[kK]\s*-\s*\$?([0-9,.]+)\s*[kK]", link["card"]
            )
            if not salary:
                continue
            seen.add(link["href"])
            title = link["title"].split("\n")[0][:120]
            parts = [part.strip() for part in title.split("•", 1)]
            records.append(
                {
                    "title": parts[0],
                    "company": parts[1] if len(parts) > 1 else "See source",
                    "url": link["href"],
                    "salary": float(salary.group(2).replace(",", "")) * 1000,
                    "salary_display": salary.group(0),
                    "remote": True,
                    "source_observation_id": observation,
                }
            )
            if len(records) >= 6:
                break
        return records

    def _failed_report(self, subtask_input, error_type):
        task = subtask_input.subtask
        failure = TypedError(
            "SESSION_UNAVAILABLE",
            f"Live Steel subtask failed ({error_type}).",
            True,
            task.subtask_id,
            [],
        )
        return WorkerReport(
            "0.5-live",
            "steel-dom",
            "deterministic-dom",
            subtask_input.request_id or subtask_input.run_id,
            task.subtask_id,
            task.goal or task.operation,
            "failed",
            "The live browser subtask did not complete.",
            None,
            [],
            {},
            {"browser_action_count": 0, "elapsed_ms": 0, "model_call_count": 0},
            [],
            subtask_input.session_handle,
            [failure],
        )


class LiveGhostBridge:
    """Validation boundary for records extracted from the live Steel pages."""

    def __init__(self):
        database = Path(
            os.getenv(
                "GHOST_DATABASE_PATH",
                Path(__file__).resolve().parents[1] / "ghostapi" / "ghostapi.sqlite3",
            )
        )
        self.store = WorkflowStore(database)

    def match(self, subtask, skills):
        request = LookupRequest(
            schema_version="0.1",
            task_id=subtask.subtask_id,
            agent_id="argus",
            site_id=subtask.site_id.removeprefix("open:"),
            operation=subtask.operation,
            parameters=subtask.parameters or {},
            required_outputs=list(subtask.expected_record_shape or []),
            allowed_actions=["navigate", "extract"],
        )
        decision = ghost_lookup(self.store, request)
        self.store.event(
            "reuse" if decision["decision"] == "reuse" else "explore",
            f"argus: workflow lookup decided {decision['decision']}.",
            {
                "task_id": subtask.subtask_id,
                "site_id": request.site_id,
                "operation": request.operation,
            },
        )
        if decision["decision"] == "reuse":
            return {
                "decision": "reuse",
                "skill": decision.get("workflow"),
                "reason": "qualified Ghost workflow matched",
            }
        return {
            "decision": "explore",
            "skill": None,
            "reason": (decision.get("reason") or {}).get(
                "message", "no compatible qualified workflow"
            ),
        }

    def validate(self, subtask, records, evidence, *, report_context=None):
        expected = (
            "staples.com"
            if subtask.subtask_id.startswith("shopping")
            else "wikivoyage.org"
            if subtask.subtask_id.startswith("travel")
            else "remotive.com"
        )
        objects = all(isinstance(record, dict) for record in records)
        sources = all(
            record.get("source_observation_id") in evidence
            for record in records
            if isinstance(record, dict)
        )
        domains = all(
            expected in (urlsplit(record.get("url", "")).hostname or "")
            for record in records
            if isinstance(record, dict)
        )
        present = bool(records)
        checks = {
            "records_are_objects": objects,
            "records_present": present,
            "records_cite_observations": sources,
            "urls_on_live_source": domains,
        }
        if subtask.subtask_id.startswith("shopping"):
            maximum = (subtask.parameters or {}).get("max_price")
            checks["price_within_user_limit"] = all(
                isinstance(r.get("price"), (int, float)) and r["price"] <= maximum
                for r in records
            )
        if subtask.subtask_id.startswith("jobs") and any(
            c.kind == "rank" and c.parameter == "salary" for c in subtask.criteria
        ):
            checks["annual_salary_present"] = all(
                isinstance(r.get("salary"), (int, float)) for r in records
            )
        failed = [name for name, passed in checks.items() if not passed]
        return {
            "status": "passed" if not failed else "failed",
            "checks": checks,
            "failed_checks": failed,
            "scope": "fresh Steel DOM records from a public website",
        }

    def compile(self, report, subtask):
        return {
            "skill_id": f"{subtask.site_id}.{subtask.operation}",
            "version": 1,
            "status": "candidate",
            "source_run": report.request_id,
            "live_source": True,
        }


def build_live_controller(store_root: str | Path) -> Controller:
    return Controller(
        LiveSteelToolbox(),
        DemoModerator(),
        LiveGhostBridge(),
        JsonStore(store_root),
        max_concurrency=4,
        plan=demo_plan,
    )
