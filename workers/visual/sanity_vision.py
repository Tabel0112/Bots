import base64
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image, ImageDraw

import httpx

from browser_subagent.uitars_agent import PROMPT_TEMPLATE, parse_action, smart_resize

img = Image.new("RGB", (1280, 800), "white")
d = ImageDraw.Draw(img)
d.rectangle([540, 360, 740, 440], fill="#2563eb")
d.text((590, 390), "Sign in", fill="white")
buf = io.BytesIO()
img.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

r = httpx.post("http://127.0.0.1:8080/v1/chat/completions", timeout=300, json={
    "model": "ui-tars",
    "temperature": 0.0,
    "max_tokens": 256,
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT_TEMPLATE.format(instruction="Click the Sign in button.")},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}],
})
r.raise_for_status()
reply = r.json()["choices"][0]["message"]["content"]
print("RAW REPLY:\n", reply)
name, kw = parse_action(reply)
print("\nparsed:", name, kw)

rh, rw = smart_resize(800, 1280)
sx, sy = 1280 / rw, 800 / rh
from browser_subagent.uitars_agent import _parse_coords
x, y = _parse_coords(kw.get("start_box") or kw.get("point"))
px, py = round(x * sx), round(y * sy)
print(f"scaled click: ({px},{py})  target box: x 540-740, y 360-440")
assert name == "click"
assert 540 <= px <= 740 and 360 <= py <= 440, "click outside button!"
print("VISION SANITY PASSED")
