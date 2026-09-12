"""Steel cloud-browser wrapper: computer actions, screenshots, CDP navigation, ghost-API trace capture."""

import base64
import io
import time

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
    def __init__(self, width=1280, height=800, api_key=None):
        self.client = Steel(steel_api_key=api_key) if api_key else Steel()
        self.width = width
        self.height = height
        self.session = None
        self.network_log = []
        self._pw = None
        self._browser = None
        self._page = None

    def start(self):
        self.session = self.client.sessions.create(
            dimensions={"width": self.width, "height": self.height},
        )
        self._connect_cdp()
        return self.session

    def _connect_cdp(self):
        from playwright.sync_api import sync_playwright

        ws = self.session.websocket_url
        if ws and "apiKey" not in ws:
            sep = "&" if "?" in ws else "?"
            ws = f"{ws}{sep}apiKey={self.client.steel_api_key}"
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(ws)
        ctx = self._browser.contexts[0]
        self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
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

    def zoom_b64(self, region):
        full = self.screenshot_b64()
        img = Image.open(io.BytesIO(base64.b64decode(full)))
        x0, y0, x1, y1 = [int(v) for v in region]
        crop = img.crop((max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)))
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def execute(self, name, inp):
        """Execute a computer_toolset member tool. Returns (text, screenshot_b64_or_None)."""
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
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        try:
            if self.session:
                self.client.sessions.release(self.session.id)
        except Exception:
            pass
