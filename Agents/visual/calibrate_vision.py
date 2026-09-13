import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx
from browser_subagent.uitars_agent import PROMPT_TEMPLATE, _parse_coords, parse_action
from PIL import Image, ImageDraw

TARGETS = [
    ("red square", (150, 120), "#dc2626"),
    ("green square", (1050, 650), "#16a34a"),
    ("blue square", (640, 400), "#2563eb"),
]

img = Image.new("RGB", (1280, 800), "white")
d = ImageDraw.Draw(img)
for label, (cx, cy), color in TARGETS:
    d.rectangle([cx - 40, cy - 40, cx + 40, cy + 40], fill=color)
buf = io.BytesIO()
img.save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

for label, (cx, cy), color in TARGETS:
    r = httpx.post("http://127.0.0.1:8080/v1/chat/completions", timeout=300, json={
        "model": "ui-tars", "temperature": 0.0, "max_tokens": 200,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT_TEMPLATE.format(instruction=f"Click the {label}.")},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
    })
    reply = r.json()["choices"][0]["message"]["content"]
    try:
        name, kw = parse_action(reply)
        x, y = _parse_coords(kw.get("start_box") or kw.get("point"))
        print(f"{label:14s} true=({cx},{cy})  model=({x:.0f},{y:.0f})  ratio=({x/cx:.3f},{y/cy:.3f})")
    except Exception as e:
        print(f"{label}: parse failed: {e} | {reply[:120]}")
