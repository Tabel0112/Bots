"""UI-TARS browser subagent: local VLM (llama.cpp / any OpenAI-compatible server)
drives a Steel cloud browser. Same ghost-API trace format as vlm_agent."""

import ast
import json
import math
import os
import re
import time

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
MAX_IMAGES_IN_CONTEXT = 4

PROMPT_TEMPLATE = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.

## Output Format
```
Thought: ...
Action: ...
```

## Action Space
click(start_box='<|box_start|>(x1,y1)<|box_end|>')
left_double(start_box='<|box_start|>(x1,y1)<|box_end|>')
right_single(start_box='<|box_start|>(x1,y1)<|box_end|>')
drag(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x3,y3)<|box_end|>')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='<|box_start|>(x1,y1)<|box_end|>', direction='down or up or right or left')
open_url(url='') #Navigate the browser directly to a URL.
zoom(start_box='<|box_start|>(x1,y1)<|box_end|>', end_box='<|box_start|>(x2,y2)<|box_end|>') #Magnify the rectangle from corner (x1,y1) to corner (x2,y2) to read small text exactly.
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished(content='xxx') # Use escape characters \\', \\", and \\n in content part to ensure we can parse the content in normal python string format.

## Note
- Use English in `Thought` part and in `finished(content=...)`.
- Write a small plan and finally summarize your next action (with its target element) in one sentence in `Thought` part.
- Small text is unreliable at full-page scale: before reporting any exact number or exact small text, zoom() into its region and read it from the magnified view.
- Coordinates in your actions ALWAYS refer to the full-page screenshot, never to a magnified zoom view.

## User Instruction
{instruction}"""


def smart_resize(height, width, factor=28, min_pixels=3136, max_pixels=12845056):
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = math.floor(height / beta / factor) * factor
        w_bar = math.floor(width / beta / factor) * factor
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return h_bar, w_bar


_COORD_RE = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)\s*\)?")


def _parse_coords(value):
    m = _COORD_RE.search(str(value))
    if not m:
        raise ValueError(f"cannot parse coordinates from {value!r}")
    return float(m.group(1)), float(m.group(2))


def parse_action(text):
    """Parse the Action: line of a UI-TARS response into (name, kwargs)."""
    m = re.search(r"Action:\s*(.+)", text, re.DOTALL)
    if not m:
        raise ValueError("no Action: line in model output")
    action_str = m.group(1).strip().splitlines()[0].strip()
    action_str = action_str.replace("<|box_start|>", "").replace("<|box_end|>", "")
    action_str = re.sub(r"</?point>", "", action_str)

    fm = re.match(r"(\w+)\((.*)\)\s*$", action_str, re.DOTALL)
    if not fm:
        raise ValueError(f"cannot parse action: {action_str!r}")
    name, argstr = fm.group(1), fm.group(2).strip()

    kwargs = {}
    if argstr:
        for am in re.finditer(r"(\w+)\s*=\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[^,]+)", argstr):
            k, v = am.group(1), am.group(2).strip()
            if v and v[0] in "'\"":
                try:
                    v = ast.literal_eval(v)
                except Exception:
                    v = v[1:-1]
            kwargs[k] = v
    return name, kwargs


def extract_thought(text):
    m = re.search(r"Thought:\s*(.*?)(?:\nAction:|$)", text, re.DOTALL)
    return m.group(1).strip() if m else None


class UITarsSubagent:
    def __init__(self, base_url=None, model="ui-tars", max_steps=40, log_dir=None,
                 width=1280, height=800, temperature=0.0, reader_url=None, reader_model="reader"):
        self.base_url = (base_url or os.environ.get("UITARS_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        # Optional second endpoint used only to read exact values off a magnified crop.
        # Measured: a 72B reader is perfect there where the 7B driver is not.
        reader = reader_url or os.environ.get("READER_BASE_URL")
        self.reader_url = reader.rstrip("/") if reader else None
        self.reader_model = reader_model
        self.model = model
        self.max_steps = max_steps
        self.log_dir = log_dir or os.path.join("runs", str(int(time.time())))
        self.width = width
        self.height = height
        self.temperature = temperature
        self._trace = None
        self._http = httpx.Client(timeout=300)

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

    def _chat(self, messages):
        self._model_calls += 1
        r = self._http.post(f"{self.base_url}/chat/completions", json={
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": 512,
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def read_value(self, image_b64, question):
        """Ask the reader model to read a value off a magnified crop.

        `question` must be a targeted question, not a free-form transcription request:
        Holo1.5 answers "...how many points does it have? Answer with the number only"
        accurately (12/12 magnified at 72B) but hallucinates on "transcribe this image".

        Returns None when no reader endpoint is configured or the call fails -- the
        driver still sees the magnified image either way."""
        if not self.reader_url:
            return None
        try:
            r = self._http.post(f"{self.reader_url}/chat/completions", json={
                "model": self.reader_model, "temperature": 0.0, "max_tokens": 128,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ]}],
            })
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            return None

    def _scale(self, x, y):
        # UI-TARS-1.5 coordinates arrive in raw screenshot pixel space (verified by
        # 3-point calibration); clamp only, no smart_resize ratio.
        return (max(0, min(self.width - 1, round(x))),
                max(0, min(self.height - 1, round(y))))

    @staticmethod
    def _img_content(b64):
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}

    def _trim_images(self, messages):
        seen = 0
        for msg in reversed(messages):
            if not isinstance(msg.get("content"), list):
                continue
            for part in list(msg["content"]):
                if part.get("type") == "image_url":
                    seen += 1
                    if seen > MAX_IMAGES_IN_CONTEXT:
                        msg["content"].remove(part)
                        if not any(p.get("type") == "text" for p in msg["content"]):
                            msg["content"].append({"type": "text", "text": "(screenshot pruned)"})

    def run(self, subtask, start_url=None):
        from .steel_browser import SteelBrowser

        os.makedirs(os.path.join(self.log_dir, "steps"), exist_ok=True)
        self._trace = open(os.path.join(self.log_dir, "trace.jsonl"), "a", encoding="utf-8")
        browser = SteelBrowser(width=self.width, height=self.height)
        result = {"success": False, "summary": "did not complete", "data": None}
        step = 0
        errors_in_a_row = 0
        action_failures_in_a_row = 0
        self._model_calls = 0
        self._last_obs = None
        t0 = time.time()
        try:
            session = browser.start()
            self._log({"type": "session_start", "session_id": session.id,
                       "viewer_url": session.session_viewer_url, "subtask": subtask,
                       "start_url": start_url, "model": f"uitars:{self.model}@{self.base_url}",
                       "dimensions": {"width": self.width, "height": self.height}})
            print(f"[subagent] live viewer: {session.session_viewer_url}", flush=True)

            if start_url:
                nav = browser.navigate(start_url)
                shot = browser.screenshot_b64()
                self._log({"type": "step", "index": step, "action": {"name": "open_url", "input": {"url": start_url}},
                           "url": nav["url"], "title": nav["title"],
                           "screenshot": self._save_screenshot(shot, step)})
                step += 1
            else:
                shot = browser.screenshot_b64()
                self._save_screenshot(shot, step)
            instruction = f"{subtask.rstrip()} Write the finished() content in English."
            messages = [{"role": "user", "content": [
                {"type": "text", "text": PROMPT_TEMPLATE.format(instruction=instruction)},
                self._img_content(shot),
            ]}]

            while step < self.max_steps:
                self._trim_images(messages)
                reply = self._chat(messages)
                messages.append({"role": "assistant", "content": reply})
                thought = extract_thought(reply)

                entry = {"type": "step", "index": step, "url": browser.current_url,
                         "model_text": (thought or reply)[:500]}
                try:
                    name, kw = parse_action(reply)
                except ValueError as e:
                    errors_in_a_row += 1
                    entry.update({"action": {"name": "unparseable", "raw": reply[:300]}, "error": str(e)})
                    self._log(entry)
                    if errors_in_a_row >= 3:
                        result = {"success": False, "summary": "aborted: 3 unparseable outputs in a row", "data": None}
                        break
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": "Your output could not be parsed. Reply with exactly one Thought/Action block using the defined action space."}]})
                    step += 1
                    continue
                errors_in_a_row = 0
                entry["action"] = {"name": name, "input": kw}

                feedback = []
                shot = None
                try:
                    if name == "finished":
                        result = {"success": True, "summary": str(kw.get("content", "done")), "data": None}
                        self._log(entry)
                        break
                    elif name == "click":
                        x, y = self._scale(*_parse_coords(kw.get("start_box") or kw.get("point")))
                        entry["element"] = browser.element_at(x, y)
                        entry["action"]["input"] = {"coordinate": [x, y]}
                        _, shot = browser.execute("left_click", {"coordinate": [x, y]})
                    elif name == "left_double":
                        x, y = self._scale(*_parse_coords(kw.get("start_box") or kw.get("point")))
                        entry["element"] = browser.element_at(x, y)
                        entry["action"]["input"] = {"coordinate": [x, y]}
                        _, shot = browser.execute("double_click", {"coordinate": [x, y]})
                    elif name == "right_single":
                        x, y = self._scale(*_parse_coords(kw.get("start_box") or kw.get("point")))
                        entry["element"] = browser.element_at(x, y)
                        entry["action"]["input"] = {"coordinate": [x, y]}
                        _, shot = browser.execute("right_click", {"coordinate": [x, y]})
                    elif name == "drag":
                        x0, y0 = self._scale(*_parse_coords(kw["start_box"]))
                        x1, y1 = self._scale(*_parse_coords(kw["end_box"]))
                        entry["action"]["input"] = {"from": [x0, y0], "to": [x1, y1]}
                        _, shot = browser.execute("left_click_drag",
                                                  {"start_coordinate": [x0, y0], "coordinate": [x1, y1]})
                    elif name == "hotkey":
                        keys = str(kw.get("key", "")).replace("+", " ").split()
                        entry["action"]["input"] = {"keys": keys}
                        _, shot = browser.execute("key", {"text": "+".join(keys)})
                    elif name == "type":
                        content = str(kw.get("content", ""))
                        submit = content.endswith("\n")
                        _, shot = browser.execute("type", {"text": content.rstrip("\n")})
                        if submit:
                            _, shot = browser.execute("key", {"text": "Return"})
                        entry["action"]["input"] = {"text": content}
                    elif name == "scroll":
                        direction = str(kw.get("direction", "down")).strip().lower()
                        inp = {"scroll_direction": direction, "scroll_amount": 5}
                        if kw.get("start_box"):
                            inp["coordinate"] = list(self._scale(*_parse_coords(kw["start_box"])))
                        entry["action"]["input"] = inp
                        _, shot = browser.execute("scroll", inp)
                    elif name == "zoom":
                        x0, y0 = self._scale(*_parse_coords(kw["start_box"]))
                        x1, y1 = self._scale(*_parse_coords(kw["end_box"]))
                        entry["action"]["input"] = {"region": [x0, y0, x1, y1]}
                        shot = browser.zoom_b64([x0, y0, x1, y1])
                        feedback.append(
                            f"magnified view of region ({x0},{y0})-({x1},{y1}); "
                            "action coordinates must still refer to the full-page screenshot")
                        reading = self.read_value(
                            shot,
                            f"This is a magnified region of a web page. {subtask.rstrip()} "
                            "Answer using only what is visible in this image.")
                        if reading:
                            entry["reader_answer"] = reading
                            feedback.append(f"A dedicated reader model reads it as: {reading}")
                    elif name == "open_url":
                        nav = browser.navigate(str(kw.get("url", "")))
                        entry["title"] = nav["title"]
                        shot = browser.screenshot_b64()
                        feedback.append(f"now at {nav['url']} ({nav['title']})")
                    elif name == "wait":
                        time.sleep(5)
                        shot = browser.screenshot_b64()
                    else:
                        feedback.append(f"unknown action {name}; use only the defined action space")
                except Exception as e:
                    entry["error"] = str(e)
                    feedback.append(f"action failed: {e}")
                    action_failures_in_a_row += 1
                    err_l = str(e).lower()
                    if "session" in err_l and ("404" in err_l or "not found" in err_l or "expired" in err_l):
                        self._log(entry)
                        result = {"success": False, "summary": f"aborted: browser session lost ({e})", "data": None}
                        break
                    if action_failures_in_a_row >= 3:
                        self._log(entry)
                        result = {"success": False,
                                  "summary": f"aborted: 3 consecutive action failures (last: {e})", "data": None}
                        break
                else:
                    action_failures_in_a_row = 0

                if shot:
                    entry["screenshot"] = self._save_screenshot(shot, step)
                self._log(entry)
                step += 1

                content = [{"type": "text", "text": " ".join(feedback)}] if feedback else []
                if shot:
                    content.append(self._img_content(shot))
                if not content:
                    content = [{"type": "text", "text": "(no visual change captured)"}]
                messages.append({"role": "user", "content": content})
            else:
                result = {"success": False, "summary": f"stopped: exceeded max_steps={self.max_steps}", "data": None}

            for rec in browser.network_log:
                self._log({"type": "network", **rec})
            metrics = {"elapsed_ms": int((time.time() - t0) * 1000),
                       "browser_action_count": step,
                       "model_call_count": self._model_calls,
                       "token_counts": None}
            self._log({"type": "session_end", "status": "success" if result["success"] else "failure",
                       "result": result, "steps": step, "metrics": metrics})
            result.update({"steps": step, "log_dir": os.path.abspath(self.log_dir),
                           "viewer_url": browser.viewer_url, "session_id": session.id,
                           "metrics": metrics})
            return result
        finally:
            browser.stop()
            if self._trace:
                self._trace.close()
