"""VLM browser subagent: receives a subtask, drives a Steel cloud browser via Claude
computer use, and writes a ghost-API trace (actions + DOM elements + network calls)."""

import json
import os
import time

import anthropic

from .steel_browser import SteelBrowser

MODEL = "claude-opus-5"
SYSTEM = """You are a browser-operating subagent. You receive one subtask from a moderator \
agent and must complete it in a real Chrome browser that you control through computer-use \
tools (screenshots, mouse, keyboard) plus a navigate tool.

Rules:
- Take a screenshot whenever you are unsure of the current page state.
- Use navigate for going to URLs instead of typing into an address bar (the browser has no visible address bar).
- Prefer precise clicks on visible elements; scroll when content is below the fold.
- When the subtask is done (or impossible), call task_complete with success true/false, a \
concise summary, and any extracted data the moderator needs.
- Be efficient: this run is recorded step-by-step to later synthesize an automated replay, \
so avoid redundant or exploratory actions once you know what to do."""

CUSTOM_TOOLS = [
    {
        "name": "navigate",
        "description": "Navigate the browser tab to a URL and wait for it to load. Returns the final URL and page title.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "go_back",
        "description": "Go back one entry in browser history.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "task_complete",
        "description": "Report the subtask as finished. Call this exactly once, when done or when the task is impossible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "summary": {"type": "string"},
                "data": {"type": "object", "description": "Any structured data extracted for the moderator."},
            },
            "required": ["success", "summary"],
        },
    },
]


def _img_block(b64):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}


class BrowserSubagent:
    def __init__(self, model=MODEL, max_steps=40, log_dir=None, width=1280, height=800):
        self.model = model
        self.max_steps = max_steps
        self.log_dir = log_dir or os.path.join("runs", str(int(time.time())))
        self.width = width
        self.height = height
        self.client = anthropic.Anthropic()
        self._trace = None

    def _log(self, record):
        record["ts"] = time.time()
        if record.get("type") == "step":
            record["step_id"] = f"step-{record['index']:03d}"
            record.setdefault("outcome", "failed" if record.get("error") else "succeeded")
            record["observation_before"] = self._last_obs
            if record.get("screenshot"):
                record["observation_after"] = record["screenshot"]
                self._last_obs = record["screenshot"]
        self._trace.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._trace.flush()

    def _save_screenshot(self, b64, step):
        import base64 as _b64
        path = os.path.join(self.log_dir, "steps", f"{step:03d}.png")
        with open(path, "wb") as f:
            f.write(_b64.b64decode(b64))
        return path

    def run(self, subtask, start_url=None):
        os.makedirs(os.path.join(self.log_dir, "steps"), exist_ok=True)
        self._trace = open(os.path.join(self.log_dir, "trace.jsonl"), "a", encoding="utf-8")
        browser = SteelBrowser(width=self.width, height=self.height)
        result = {"success": False, "summary": "did not complete", "data": None}
        step = 0
        self._last_obs = None
        try:
            session = browser.start()
            self._log({"type": "session_start", "session_id": session.id,
                       "viewer_url": session.session_viewer_url, "subtask": subtask,
                       "start_url": start_url, "model": self.model,
                       "dimensions": {"width": self.width, "height": self.height}})
            print(f"[subagent] live viewer: {session.session_viewer_url}", flush=True)

            first_content = [{"type": "text", "text": f"Subtask: {subtask}"}]
            if start_url:
                nav = browser.navigate(start_url)
                self._log({"type": "step", "index": step, "action": {"name": "navigate", "input": {"url": start_url}},
                           "url": nav["url"], "title": nav["title"]})
                shot = browser.screenshot_b64()
                first_content.append({"type": "text", "text": f"The browser is at {nav['url']} ({nav['title']}). Current screenshot:"})
                first_content.append(_img_block(shot))
                self._save_screenshot(shot, step)
                step += 1
            messages = [{"role": "user", "content": first_content}]

            while step < self.max_steps:
                response = self.client.beta.messages.create(
                    model=self.model,
                    max_tokens=16000,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    cache_control={"type": "ephemeral"},
                    system=SYSTEM,
                    tools=[{"type": "computer_toolset_20260801"}] + CUSTOM_TOOLS,
                    messages=messages,
                )
                if response.stop_reason == "refusal":
                    detail = getattr(response.stop_details, "explanation", None) if response.stop_details else None
                    result = {"success": False, "summary": f"model refused: {detail}", "data": None}
                    break

                messages.append({"role": "assistant", "content": response.content})
                model_text = " ".join(b.text for b in response.content if b.type == "text")

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    result = {"success": True, "summary": model_text or "finished without explicit report", "data": None}
                    break

                tool_results = []
                done = False
                for block in tool_uses:
                    name, inp = block.name, block.input
                    is_member = getattr(block, "toolset_name", None) == "computer"
                    entry = {"type": "step", "index": step, "action": {"name": name, "input": inp},
                             "url": browser.current_url, "model_text": model_text[:500] or None}

                    try:
                        if name == "task_complete":
                            result = {"success": bool(inp.get("success")), "summary": inp.get("summary", ""),
                                      "data": inp.get("data")}
                            content = "acknowledged"
                            done = True
                        elif name == "navigate":
                            nav = browser.navigate(inp["url"])
                            shot = browser.screenshot_b64()
                            entry["screenshot"] = self._save_screenshot(shot, step)
                            entry["title"] = nav["title"]
                            content = [{"type": "text", "text": f"now at {nav['url']} ({nav['title']})"}, _img_block(shot)]
                        elif name == "go_back":
                            nav = browser.go_back()
                            shot = browser.screenshot_b64()
                            entry["screenshot"] = self._save_screenshot(shot, step)
                            content = [{"type": "text", "text": f"now at {nav['url']}"}, _img_block(shot)]
                        elif is_member:
                            if name in ("left_click", "right_click", "middle_click", "double_click",
                                        "triple_click") and inp.get("coordinate"):
                                entry["element"] = browser.element_at(*inp["coordinate"])
                            text, shot = browser.execute(name, inp)
                            if shot and name not in ("screenshot", "zoom"):
                                entry["screenshot"] = self._save_screenshot(shot, step)
                            if shot:
                                content = [_img_block(shot)] if name in ("screenshot", "zoom") else \
                                          [{"type": "text", "text": text}, _img_block(shot)]
                            else:
                                content = text
                        else:
                            content = f"unknown tool {name}"
                    except Exception as e:
                        entry["error"] = str(e)
                        content = f"action failed: {e}"
                        tr = {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": True}
                        if is_member:
                            tr["toolset_name"] = "computer"
                        tool_results.append(tr)
                        self._log(entry)
                        step += 1
                        continue

                    tr = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    if is_member:
                        tr["toolset_name"] = "computer"
                    tool_results.append(tr)
                    self._log(entry)
                    step += 1

                messages.append({"role": "user", "content": tool_results})
                if done:
                    break
            else:
                result = {"success": False, "summary": f"stopped: exceeded max_steps={self.max_steps}", "data": None}

            for rec in browser.network_log:
                self._log({"type": "network", **rec})

            self._log({"type": "session_end", "status": "success" if result["success"] else "failure",
                       "result": result, "steps": step})
            result.update({
                "steps": step,
                "log_dir": os.path.abspath(self.log_dir),
                "viewer_url": browser.viewer_url,
                "session_id": session.id,
            })
            return result
        finally:
            browser.stop()
            if self._trace:
                self._trace.close()


def run_subtask(subtask, start_url=None, **kwargs):
    return BrowserSubagent(**kwargs).run(subtask, start_url=start_url)
