"""Shared Playwright mechanics for local Chromium and Steel CDP sessions."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode, urljoin
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from ..config import Settings, SiteConfig
from ..policy import guard_url, resolve_element
from ..schemas import Decision, Observation, SubtaskRequest, WorkerError, uid
from ..schemas import FailureCode as C

OBSERVE_SCRIPT = Path(__file__).with_name("observe.js").read_text("utf-8")


class BrowserAdapter:
    def __init__(self, task: SubtaskRequest, site: SiteConfig, settings: Settings):
        self.task, self.site, self.settings = task, site, settings
        self.playwright = self.browser = self.context = self.page = self.steel = None
        self.session_ref = task.session.session_ref
        self.attribute = "data-argus-" + uid()
        self.blocked: WorkerError | None = None
        self.pending_redirect: str | None = None
        self.routed = False
        self.action_in_progress = False
        self.owns = task.session.ownership == "worker"

    async def start(self):
        try:
            if self.settings.browser == "local" and not self.owns:
                raise WorkerError(C.SESSION_UNAVAILABLE, "Existing sessions require Steel mode.")
            if self.settings.browser == "steel":
                from steel import AsyncSteel

                key = os.getenv("STEEL_API_KEY")
                if not key:
                    raise WorkerError(C.SESSION_UNAVAILABLE, "STEEL_API_KEY is not configured.")
                self.steel = AsyncSteel(steel_api_key=key, max_retries=0, timeout=15)
                if self.owns:
                    # Known before the network call, so even a cancelled create can be released.
                    # Steel accepts caller-provided IDs in canonical UUID format.
                    self.session_ref = str(uuid4())
                    # SDK api_timeout is Steel's session lifetime; timeout is HTTP timeout.
                    session = await self.steel.sessions.create(
                        session_id=self.session_ref,
                        api_timeout=(self.task.budgets.max_runtime_seconds + 30) * 1000,
                    )
                    self.session_ref = session.id
            self.playwright = await async_playwright().start()
            if self.settings.browser == "local":
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    executable_path=self.settings.browser_executable,
                )
                self.context = await self.browser.new_context(
                    service_workers="block", accept_downloads=False
                )
                self.page = await self.context.new_page()
                self.session_ref = "local-" + uid()
            else:
                query = urlencode(
                    {"apiKey": os.environ["STEEL_API_KEY"], "sessionId": self.session_ref}
                )
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    "wss://connect.steel.dev?" + query,
                    timeout=self.settings.browser_timeout_seconds * 1000,
                )
                if not self.browser.contexts:
                    raise WorkerError(C.SESSION_UNAVAILABLE, "Steel returned no browser context.")
                self.context = self.browser.contexts[0]
                if len(self.context.pages) > 1:
                    raise WorkerError(
                        C.SESSION_UNAVAILABLE, "Supply a dedicated session with one page."
                    )
                self.page = (
                    self.context.pages[0] if self.context.pages else await self.context.new_page()
                )
                # Existing service workers can bypass request interception.
                if self.context.service_workers:
                    raise WorkerError(
                        C.SESSION_UNAVAILABLE,
                        "Session has active service workers; use a fresh session.",
                    )
                cdp = await self.context.new_cdp_session(self.page)
                await cdp.send("Network.setBypassServiceWorker", {"bypass": True})
            self.page.set_default_timeout(self.settings.browser_timeout_seconds * 1000)
            await self.context.route("**/*", self._route)
            self.routed = True
            await self.page.route_web_socket("**/*", lambda socket: socket.close())
            self.page.on("dialog", lambda dialog: dialog.dismiss())
            if self.owns or self.task.start_url:
                await self.navigate(self.task.start_url or self.site.start_url)
            else:
                guard_url(self.page.url, self.site, self.task)
        except WorkerError:
            raise
        except Exception:
            # SDK and browser errors can include credential-bearing connection URLs.
            raise WorkerError(
                C.SESSION_UNAVAILABLE, "Could not initialize the browser session."
            ) from None

    async def _route(self, route):
        request = route.request
        try:
            if request.method not in {"GET", "HEAD"}:
                raise WorkerError(C.ACTION_REJECTED, "Non-read-only network request was blocked.")
            guard_url(
                request.url, self.site, self.task, resource=not request.is_navigation_request()
            )
        except WorkerError as exc:
            # Abort ambient analytics/XHR without poisoning later observations. During
            # an explicit worker interaction, conservatively treat writes as its effect.
            if request.is_navigation_request() or (
                self.action_in_progress and request.method not in {"GET", "HEAD"}
            ):
                self.blocked = exc
            await route.abort()
            return
        # Playwright only routes the first request in an HTTP redirect chain. Never
        # give Chromium an automatic redirect: queue each main-page hop for a fresh
        # guarded goto. This preserves both the final page URL and per-hop policy.
        try:
            response = await route.fetch(
                max_redirects=0, timeout=self.settings.browser_timeout_seconds * 1000
            )
            if 300 <= response.status < 400 and response.headers.get("location"):
                destination = urljoin(request.url, response.headers["location"])
                guard_url(
                    destination, self.site, self.task, resource=not request.is_navigation_request()
                )
                if request.is_navigation_request() and request.frame == self.page.main_frame:
                    self.pending_redirect = destination
                    await route.fulfill(
                        status=200, content_type="text/html", body="<html><body></body></html>"
                    )
                else:
                    # Asset/iframe redirects are unsupported; don't follow them implicitly.
                    await route.abort()
                return
            await route.fulfill(response=response)
        except WorkerError as exc:
            if request.is_navigation_request():
                self.blocked = exc
            await route.abort()
        except PlaywrightError:
            await route.abort()

    def _check_blocked(self):
        if self.blocked:
            raise self.blocked

    async def navigate(self, url: str):
        guard_url(url, self.site, self.task)
        try:
            await self.page.goto(url, wait_until="domcontentloaded")
            await self._follow_redirects()
        except PlaywrightTimeout:
            self._check_blocked()
            raise WorkerError(
                C.NAVIGATION_TIMEOUT, "Navigation did not settle in time.", True
            ) from None
        except PlaywrightError:
            self._check_blocked()
            raise WorkerError(
                C.NAVIGATION_TIMEOUT, "Navigation could not load the page.", True
            ) from None
        self._check_blocked()
        guard_url(self.page.url, self.site, self.task)

    async def _follow_redirects(self):
        for _ in range(10):
            self._check_blocked()
            if not self.pending_redirect:
                return
            url, self.pending_redirect = self.pending_redirect, None
            guard_url(url, self.site, self.task)
            await self.page.goto(url, wait_until="domcontentloaded")
        raise WorkerError(C.NO_PROGRESS, "HTTP redirect limit exceeded.")

    async def _snapshot(self):
        self._check_blocked()
        await self._follow_redirects()
        guard_url(self.page.url, self.site, self.task)
        data = await self.page.evaluate(
            OBSERVE_SCRIPT,
            {
                "attribute": self.attribute,
                "maxElements": 220,
                "maxText": 12000,
                "config": self.site.model_dump(mode="json"),
            },
        )
        # Remove credential-looking URLs instead of putting them in model context.
        for element in data["elements"]:
            if element.get("href"):
                try:
                    guard_url(element["href"], self.site, self.task)
                except WorkerError:
                    element["href"] = None
                    element["permitted_actions"] = []
        return data

    @staticmethod
    def _hash(data, url):
        return hashlib.sha256(json.dumps([url, data], sort_keys=True).encode()).hexdigest()

    async def observe(self, execution_id: str) -> Observation:
        data = await self._snapshot()
        return Observation(
            execution_id=execution_id,
            run_id=self.task.run_id,
            url=self.page.url,
            content_hash=self._hash(data, self.page.url),
            **data,
        )

    async def assert_fresh(self, obs: Observation):
        current = await self._snapshot()
        if self._hash(current, self.page.url) != obs.content_hash:
            raise WorkerError(
                C.STALE_OBSERVATION, "Page changed after observation; observe again.", True
            )

    async def execute(self, decision: Decision, obs: Observation):
        await self.assert_fresh(obs)
        a, kind = decision.arguments, decision.action_type
        self.action_in_progress = kind in {"navigate", "fill", "select", "click"}
        try:
            if kind == "navigate":
                await self.navigate(a.url)
            elif kind in {"fill", "select", "click", "inspect_element"}:
                element = resolve_element(obs, a.target_ref)
                locator = self.page.locator(f'[{self.attribute}="{element.ref}"]')
                count = await locator.count()
                if count != 1:
                    raise WorkerError(
                        C.TARGET_AMBIGUOUS if count > 1 else C.TARGET_NOT_FOUND,
                        "Target is no longer unique and available.",
                        True,
                    )
                if not await locator.is_visible():
                    raise WorkerError(C.TARGET_NOT_FOUND, "Target is no longer visible.", True)
                # Recheck the trusted CSS permission against the live element, not the model.
                if kind != "inspect_element":
                    selectors = [r.selector for r in self.site.controls if kind in r.actions]
                    if not await locator.evaluate(
                        "(el, selectors) => selectors.some(s=>el.matches(s))", selectors
                    ):
                        raise WorkerError(
                            C.ACTION_REJECTED,
                            "Live control no longer matches site permissions.",
                            True,
                        )
                if kind == "fill":
                    await locator.fill(a.value)
                elif kind == "select":
                    await locator.select_option(value=a.value)
                elif kind == "click":
                    await locator.click()
                else:
                    return {"element": element.model_dump(mode="json")}
            elif kind == "wait_for":
                if a.target_ref:
                    element = resolve_element(obs, a.target_ref)
                    await self.page.locator(f'[{self.attribute}="{element.ref}"]').wait_for(
                        state="visible"
                    )
                else:
                    await self.page.locator(self.site.results_selector).wait_for(state="visible")
            await self._follow_redirects()
            self._check_blocked()
            return {"executed": True}
        except PlaywrightTimeout:
            self._check_blocked()
            raise WorkerError(
                C.PRECONDITION_FAILED, "Browser action exceeded its bounded wait.", True
            ) from None
        except PlaywrightError:
            self._check_blocked()
            raise WorkerError(
                C.TARGET_NOT_FOUND, "Browser could not act on the current target.", True
            ) from None
        finally:
            self.action_in_progress = False

    async def settle(self, decision: Decision, before: Observation):
        """Bounded wait for the proposed change; no networkidle or unbounded sleeps."""
        deadline = asyncio.get_running_loop().time() + min(2, self.settings.browser_timeout_seconds)
        while asyncio.get_running_loop().time() < deadline:
            data = await self._snapshot()
            if (
                decision.arguments.expected_change == "results_ready"
                and data["signals"]["results_ready"]
            ):
                return
            if self._hash(data, self.page.url) != before.content_hash:
                return
            if decision.arguments.expected_change in {"unchanged", "visible"}:
                return
            await asyncio.sleep(0.1)

    async def close(self) -> str:
        failed = False
        if self.context and self.routed:
            try:
                await self.context.unroute("**/*", self._route)
            except Exception:
                failed = True
        # stop() disconnects CDP without issuing Browser.close to an ARGUS-owned session.
        if self.settings.browser == "local" and self.browser:
            try:
                await self.browser.close()
            except Exception:
                failed = True
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                failed = True
        release = self.owns or self.task.session.close_on_finish
        if self.steel:
            try:
                if release and self.session_ref:
                    await self.steel.sessions.release(self.session_ref)
            except Exception:
                failed = True
            finally:
                await self.steel.close()
        if failed:
            return "cleanup_failed"
        return ("released" if release else "retained") if self.session_ref else "not_started"
