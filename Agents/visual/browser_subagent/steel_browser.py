"""Steel cloud-browser wrapper: computer actions, screenshots, CDP navigation, ghost-API trace capture."""

import base64
import io
import time
from uuid import uuid4

from PIL import Image
from steel import Steel

KEY_MAP = {
    "Return": "Enter",
    "KP_Enter": "Enter",
    "BackSpace": "Backspace",
    "Page_Down": "PageDown",
    "Page_Up": "PageUp",
    "Down": "ArrowDown",
    "Up": "ArrowUp",
    "Left": "ArrowLeft",
    "Right": "ArrowRight",
    "Escape": "Escape",
    "Tab": "Tab",
    "Delete": "Delete",
    "Home": "Home",
    "End": "End",
    "space": " ",
    "ctrl": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "cmd": "Meta",
    "super": "Meta",
    "win": "Meta",
}


def normalize_key(k):
    return KEY_MAP.get(k, KEY_MAP.get(k.lower(), k))


class SteelBrowser:
    def __init__(self, width=1280, height=800, api_key=None, session_timeout_ms=900_000,
                 session_ref=None, close_on_finish=False, task=None, site=None):
        options = {"max_retries": 0, "timeout": 10} if task else {}
        self.client = Steel(steel_api_key=api_key, **options) if api_key else Steel(**options)
        self.width = width
        self.height = height
        self.session_timeout_ms = session_timeout_ms
        self.view_offset = (0, 0)
        self.session = None
        self.network_log = []
        self._pw = None
        self._browser = None
        self._page = None
        self.session_ref = session_ref
        self.owns_session = session_ref is None or close_on_finish
        self.task, self.site = task, site
        self.cleanup_status = "not_started"
        self.ghost_action = None

    def start(self):
        if self.session_ref:
            self.session = self.client.sessions.retrieve(self.session_ref)
        else:
            self.session_ref = str(uuid4())
            self.session = self.client.sessions.create(
                session_id=self.session_ref,
                dimensions={"width": self.width, "height": self.height},
                api_timeout=self.session_timeout_ms,
            )
        self._connect_cdp()
        if self.task:
            self._page.context.route("**/*", self._guard_request)
        self._measure_view_offset()
        return self.session

    def _guard_request(self, route):
        from Agents.browser_worker.policy import guard_url
        try:
            if route.request.method not in {"GET", "HEAD"}:
                raise ValueError("write request")
            guard_url(route.request.url, self.site, self.task,
                      resource=not route.request.is_navigation_request())
            # Do not allow implicit redirect hops to escape the domain guard.
            response = route.fetch(max_redirects=0, timeout=10000)
            if 300 <= response.status < 400:
                route.abort()
            else:
                route.fulfill(response=response)
        except Exception:
            route.abort()

    def _measure_view_offset(self):
        # Steel mouse coordinates are in full-window screenshot space; DOM APIs use
        # page-viewport space. Measure the constant offset once per session.
        self.view_offset = (0, 0)
        try:
            self._page.evaluate("window.__cal=null; addEventListener('mousemove', e => window.__cal=[e.clientX,e.clientY])")
            self._computer(action="move_mouse", coordinates=[100, 100])
            time.sleep(0.3)
            got = self._page.evaluate("window.__cal")
            if got:
                self.view_offset = (100 - got[0], 100 - got[1])
        except Exception:
            pass

    def _connect_cdp(self):
        from playwright.sync_api import sync_playwright

        ws = self.session.websocket_url
        if ws and "apiKey" not in ws:
            sep = "&" if "?" in ws else "?"
            ws = f"{ws}{sep}apiKey={self.client.steel_api_key}"
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(ws, timeout=10000 if self.task else 30000)
        ctx = self._browser.contexts[0]
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if self.task:
            self._page.set_default_timeout(10000)
        ctx.on("response", self._on_response)

    def _on_response(self, resp):
        try:
            req = resp.request
            if req.resource_type not in ("xhr", "fetch", "document"):
                return
            post = req.post_data
            self.network_log.append({
                "ts": time.time(),
                "method": req.method,
                "url": req.url,
                "status": resp.status,
                "resource_type": req.resource_type,
                "post_data": post[:2000] if post else None,
            })
        except Exception:
            pass

    @property
    def viewer_url(self):
        return self.session.session_viewer_url if self.session else None

    @property
    def current_url(self):
        try:
            return self._page.url
        except Exception:
            return None

    @property
    def page_title(self):
        try:
            return self._page.title()
        except Exception:
            return None

    def navigate(self, url):
        if self.task:
            from Agents.browser_worker.policy import guard_url
            if "navigate" not in self.task.allowed_actions:
                raise ValueError("Navigation is outside the task action budget.")
            guard_url(url, self.site, self.task)
        self.ghost_action = {"action": "navigate", "value": url, "target": None,
                             "expected_state": {"change": "changed"}}
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        return {"url": self._page.url, "title": self.page_title}

    def go_back(self):
        self._page.go_back(wait_until="domcontentloaded", timeout=15000)
        return {"url": self._page.url, "title": self.page_title}

    def element_at(self, x, y):
        x, y = x - self.view_offset[0], y - self.view_offset[1]
        try:
            return self._page.evaluate(
                """([x, y]) => {
                    const e = document.elementFromPoint(x, y);
                    if (!e) return null;
                    let sel = [], n = e;
                    while (n && n.nodeType === 1 && sel.length < 6) {
                        let s = n.tagName.toLowerCase();
                        if (n.id) { sel.unshift(s + '#' + n.id); break; }
                        sel.unshift(s);
                        n = n.parentElement;
                    }
                    return {
                        tag: e.tagName.toLowerCase(),
                        id: e.id || null,
                        name: e.getAttribute('name'),
                        href: e.getAttribute('href'),
                        type: e.getAttribute('type'),
                        text: (e.innerText || e.value || '').slice(0, 100),
                        selector: sel.join(' > '),
                        role: e.getAttribute('role') || ({BUTTON:'button', A:'link', INPUT:'textbox', TEXTAREA:'textbox', SELECT:'combobox'})[e.tagName] || null,
                        label: e.getAttribute('aria-label') || [...(e.labels || [])].map(l=>l.textContent.trim()).join(' ') || e.getAttribute('placeholder') || (e.tagName === 'BUTTON' || e.tagName === 'A' ? e.textContent.trim() : null),
                    };
                }""",
                [x, y],
            )
        except Exception:
            return None

    def _computer(self, **kwargs):
        resp = self.client.sessions.computer(self.session.id, **kwargs)
        if resp.error:
            raise RuntimeError(resp.error)
        return resp

    def screenshot_b64(self):
        resp = self._computer(action="take_screenshot")
        return resp.base64_image

    def zoom_b64(self, region, min_side=24):
        full = self.screenshot_b64()
        img = Image.open(io.BytesIO(base64.b64decode(full)))
        x0, y0, x1, y1 = [int(v) for v in region]
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        # Models often describe a region as a line rather than a box (UI-TARS-72B did
        # this in 3/12 trials, e.g. (54,288)-(231,286)). Grow such a region about its
        # centre instead of failing on an empty crop.
        if x1 - x0 < min_side:
            c = (x0 + x1) // 2
            x0, x1 = c - min_side // 2, c + min_side // 2
        if y1 - y0 < min_side:
            c = (y0 + y1) // 2
            y0, y1 = c - min_side // 2, c + min_side // 2
        crop = img.crop((max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)))
        if crop.width and crop.width < 800:
            factor = min(4, max(2, round(800 / crop.width)))
            crop = crop.resize((crop.width * factor, crop.height * factor), Image.LANCZOS)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def execute(self, name, inp):
        """Execute a computer_toolset member tool. Returns (text, screenshot_b64_or_None)."""
        self.ghost_action = None
        if self.task:
            self._guard_action(name, inp)
            if name in {"type", "select"}:
                # Integrated typing has replacement semantics, matching Ghost fill.
                if name == "type":
                    self._page.locator(":focus").fill(inp["text"])
                else:
                    self._page.locator(":focus").select_option(value=inp["text"])
                return "OK", self.screenshot_b64()
        mods = [normalize_key(m) for m in inp["text"].split("+")] if inp.get("text") and name != "type" and name != "key" and name != "hold_key" else None

        if name == "screenshot":
            return "screenshot", self.screenshot_b64()
        if name == "zoom":
            return "zoom", self.zoom_b64(inp["region"])
        if name == "cursor_position":
            resp = self._computer(action="get_cursor_position")
            return resp.output or "unknown", None
        if name == "wait":
            self._computer(action="wait", duration=min(float(inp.get("duration", 1)), 30))
            return "waited", self.screenshot_b64()

        if name in ("left_click", "right_click", "middle_click", "double_click", "triple_click"):
            button = {"right_click": "right", "middle_click": "middle"}.get(name, "left")
            clicks = {"double_click": 2, "triple_click": 3}.get(name, 1)
            kwargs = {"action": "click_mouse", "button": button, "num_clicks": clicks}
            if inp.get("coordinate"):
                kwargs["coordinates"] = inp["coordinate"]
            if mods:
                kwargs["hold_keys"] = mods
            self._computer(**kwargs)
        elif name == "left_click_drag":
            self._computer(action="drag_mouse", path=[inp["start_coordinate"], inp["coordinate"]])
        elif name == "mouse_move":
            self._computer(action="move_mouse", coordinates=inp["coordinate"])
        elif name == "left_mouse_down":
            self._computer(action="click_mouse", button="left", click_type="down")
        elif name == "left_mouse_up":
            self._computer(action="click_mouse", button="left", click_type="up")
        elif name == "scroll":
            amount = int(inp.get("scroll_amount", 3)) * 100
            direction = inp.get("scroll_direction", "down")
            dx = amount if direction == "right" else -amount if direction == "left" else 0
            dy = amount if direction == "down" else -amount if direction == "up" else 0
            kwargs = {"action": "scroll", "delta_x": dx, "delta_y": dy}
            if inp.get("coordinate"):
                kwargs["coordinates"] = inp["coordinate"]
            self._computer(**kwargs)
        elif name == "type":
            self._computer(action="type_text", text=inp["text"])
        elif name == "key":
            keys = [normalize_key(k) for k in inp["text"].split("+")]
            for _ in range(int(inp.get("repeat", 1))):
                self._computer(action="press_key", keys=keys)
        elif name == "hold_key":
            self._computer(action="press_key", keys=[normalize_key(inp["text"])],
                           duration=min(float(inp.get("duration", 1)), 30))
        else:
            raise ValueError(f"unsupported computer action: {name}")

        time.sleep(0.3)
        return "OK", self.screenshot_b64()

    def stop(self):
        failed = False
        try:
            if self._page and self.task:
                self._page.context.unroute("**/*", self._guard_request)
            if self._pw:
                self._pw.stop()
        except Exception:
            failed = True
        try:
            if self.owns_session and self.session_ref:
                self.client.sessions.release(self.session_ref)
        except Exception:
            failed = True
        self.cleanup_status = "cleanup_failed" if failed else "released" if self.owns_session else "retained"
        self.client.close()
        return self.cleanup_status

    def _guard_action(self, name, inp):
        if name in {"screenshot", "zoom", "cursor_position"}:
            self.ghost_action = {"inspection": True}
            return
        kind = {"left_click": "click", "type": "fill", "select": "select", "wait": "wait_for"}.get(name)
        if kind not in self.task.allowed_actions:
            raise ValueError("Unsupported visual action for this read-only task.")
        if kind == "wait_for":
            self.ghost_action = {"action": "wait", "expected_state": {"change": "results_ready"}}
            return
        selectors = [r.selector for r in self.site.controls if kind in r.actions]
        if kind == "click":
            x, y = inp.get("coordinate", [-1, -1])
            x, y = x - self.view_offset[0], y - self.view_offset[1]
            focus_selectors = [r.selector for r in self.site.controls if set(r.actions) & {"fill", "select"}]
            focus = self._page.evaluate(
                "([x,y,sels]) => {const e=document.elementFromPoint(x,y); return !!e && sels.some(s=>e.matches(s));}",
                [x, y, focus_selectors])
            allowed = focus or self._page.evaluate(
                "([x,y,sels]) => {const e=document.elementFromPoint(x,y); return !!e && sels.some(s=>e.matches(s) || !!e.closest(s));}",
                [x, y, selectors])
            element = self.element_at(*inp["coordinate"]) or {}
            self.ghost_action = {"inspection": True} if focus else {
                "action": "click", "target": {"strategy": "semantic", "role": element.get("role"), "label": element.get("label")},
                "expected_state": {"change": "results_ready"}}
        else:
            allowed = self._page.evaluate(
                "sels => sels.some(s=>document.activeElement.matches(s))", selectors)
            rules = self._page.evaluate(
                "rules => rules.filter(r=>document.activeElement.matches(r.selector))",
                [r.model_dump() for r in self.site.controls if kind in r.actions])
            parameters = {r["parameter"] for r in rules if r.get("parameter")}
            allowed = allowed and len(parameters) == 1 and inp.get("text") == str(self.task.parameters.get(next(iter(parameters), "")))
            box = self._page.locator(":focus").bounding_box()
            element = self.element_at(box["x"] + box["width"] / 2 + self.view_offset[0],
                                      box["y"] + box["height"] / 2 + self.view_offset[1]) if box else {}
            parameter = next(iter(parameters), "")
            self.ghost_action = {"action": kind,
                "target": {"strategy": "semantic", "role": (element or {}).get("role"), "label": (element or {}).get("label")},
                "value": self.task.parameters.get(parameter), "input_parameter": parameter,
                "expected_state": {"change": "value_equals"}}
        if not allowed:
            raise ValueError("Visual target or input is outside the configured task controls.")
