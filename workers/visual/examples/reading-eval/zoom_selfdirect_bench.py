#!/usr/bin/env python3
"""Does the driver model *choose* to zoom before reporting an exact value?

This measures self-direction, not perception. Reading accuracy is already settled
(see cluster_bench.py): every model tested reads a magnified crop near-perfectly and
a full page unreliably. The open question is whether the agent invokes zoom() on its
own when the task needs an exact number -- UI-TARS-1.5-7B does not, in a live run it
called zoom zero times over 8 steps and misread the value anyway.

Protocol per trial, using the production UI-TARS prompt with zoom in the action space:
the agent is shown the full page and asked for an exact value, then run as a real loop
(up to --max-turns). A zoom() is answered with the magnified crop of the region it asked
for; any other action is a no-op that re-shows the page. The trial ends at finished().

Reports zoom_rate (the headline: did it choose to magnify at all) and accuracy.

Measured, image 9d4f489a6dc239c8, UI-TARS Q4_K_M:
  7B   zoom_rate  1/12 -- and that one region was degenerate
  72B  zoom_rate 12/12 -- self-direction scales; note 3/12 of its regions were
       lines rather than boxes, which magnify() and steel_browser both now widen.

    pip install --user pillow httpx
    python3 zoom_selfdirect_bench.py --base-url http://127.0.0.1:8080/v1 --label uitars-72b
"""

import argparse
import ast
import base64
import hashlib
import io
import json
import re
import sys

import httpx
from PIL import Image, ImageDraw

W, H = 1280, 800
ROW_H = 46
TOP = 40
STORIES = [
    ("Make your first edit to OpenStreetMap", 128, 25),
    ("LG denies TV spying claims about tracking", 212, 191),
    ("Nvidia is the central bank of AI", 229, 167),
    ("Will There Be a 7G?", 45, 62),
    ("An open letter to Dario on open weights", 105, 88),
    ("Stabilizing Rust's Never Type", 21, 12),
    ("We must pace the frontier", 345, 430),
    ("Microcode in Intel's 8087 floating-point chip", 55, 17),
    ("IKEA made a mod for Skyrim", 486, 122),
    ("Google's anti-scraping update", 582, 458),
    ("A build visualizer for compile times", 31, 8),
    ("A Mathematical Framework for Transformers", 58, 12),
]

# The production action space, verbatim from browser_subagent/uitars_agent.py.
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

_COORD = re.compile(r"\(?\s*(\d+(?:\.\d+)?)\s*[, ]\s*(\d+(?:\.\d+)?)\s*\)?")


def parse_action(text):
    m = re.search(r"Action:\s*(.+)", text, re.DOTALL)
    if not m:
        raise ValueError("no Action: line")
    s = m.group(1).strip().splitlines()[0].strip()
    s = s.replace("<|box_start|>", "").replace("<|box_end|>", "")
    s = re.sub(r"</?point>", "", s)
    fm = re.match(r"(\w+)\((.*)\)\s*$", s, re.DOTALL)
    if not fm:
        raise ValueError(f"unparseable: {s!r}")
    name, argstr = fm.group(1), fm.group(2).strip()
    kw = {}
    for am in re.finditer(r"(\w+)\s*=\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|[^,]+)", argstr):
        k, v = am.group(1), am.group(2).strip()
        if v and v[0] in "'\"":
            try:
                v = ast.literal_eval(v)
            except Exception:
                v = v[1:-1]
        kw[k] = v
    return name, kw


def coords(v):
    m = _COORD.search(str(v))
    if not m:
        raise ValueError(f"no coords in {v!r}")
    return float(m.group(1)), float(m.group(2))


def build_image():
    img = Image.new("RGB", (W, H), "#f6f6ef")
    d = ImageDraw.Draw(img)
    for i, (title, points, comments) in enumerate(STORIES):
        y = TOP + i * ROW_H
        d.text((30, y), f"{i + 1}.", fill="#828282")
        d.text((64, y), title, fill="#000000")
        d.text((80, y + 14),
               f"{points} points by user{i} {i + 1} hours ago | hide | {comments} comments",
               fill="#828282")
    return img


def magnify(img, box, target_w=900, min_side=24):
    x0, y0, x1, y1 = box
    x0, x1 = sorted((int(x0), int(x1)))
    y0, y1 = sorted((int(y0), int(y1)))
    # Models often give a line rather than a box (UI-TARS-72B did this in 3/12 trials);
    # grow it about its centre rather than failing, matching steel_browser.zoom_b64.
    if x1 - x0 < min_side:
        c = (x0 + x1) // 2
        x0, x1 = c - min_side // 2, c + min_side // 2
    if y1 - y0 < min_side:
        c = (y0 + y1) // 2
        y0, y1 = c - min_side // 2, c + min_side // 2
    x0, x1 = max(0, x0), min(img.width, x1)
    y0, y1 = max(0, y0), min(img.height, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    crop = img.crop((x0, y0, x1, y1))
    if crop.width < target_w:
        f = min(4, max(2, round(target_w / crop.width)))
        crop = crop.resize((crop.width * f, crop.height * f), Image.LANCZOS)
    return crop


def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def chat(client, base_url, model, parts):
    content = []
    for p in parts:
        content.append({"type": "text", "text": p} if isinstance(p, str)
                       else {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(p)}"}})
    r = client.post(f"{base_url}/chat/completions", json={
        "model": model, "temperature": 0.0, "max_tokens": 256,
        "messages": [{"role": "user", "content": content}],
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def first_int(t):
    m = re.search(r"\d+", str(t).replace(",", ""))
    return int(m.group()) if m else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--model", default="vlm")
    p.add_argument("--label", default="model")
    p.add_argument("--max-turns", type=int, default=6,
                   help="let the agent iterate (zoom, look, zoom again, answer)")
    args = p.parse_args()

    img = build_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
    print(f"image sha256[:16] = {digest}")
    print(f"protocol: turn 1 full page -> did it zoom?  turn 2 (if zoomed) -> was it right?\n")

    client = httpx.Client(timeout=600)
    rows = []
    for title, points, _c in STORIES:
        instruction = (f'Report the exact number of points for the row titled "{title}". '
                       "The points are rendered in small text.")
        rec = {"title": title, "expected": points, "zoomed": False,
               "action": None, "correct": False, "turns": 0, "path": [], "note": ""}
        # Let the agent actually run its loop: it may zoom, look, zoom again, then
        # answer. Stopping at turn 2 measures impatience, not capability.
        parts = [PROMPT_TEMPLATE.format(instruction=instruction), img]
        try:
            for turn in range(args.max_turns):
                rec["turns"] = turn + 1
                reply = chat(client, args.base_url.rstrip("/"), args.model, parts)
                name, kw = parse_action(reply)
                rec["path"].append(name)
                if rec["action"] is None:
                    rec["action"] = name
                if name == "finished":
                    rec["correct"] = first_int(kw.get("content")) == points
                    break
                if name == "zoom":
                    rec["zoomed"] = True
                    x0, y0 = coords(kw.get("start_box"))
                    x1, y1 = coords(kw.get("end_box"))
                    crop = magnify(img, (x0, y0, x1, y1))
                    if crop is None:
                        rec["note"] = f"unusable region ({x0},{y0})-({x1},{y1})"
                        break
                    parts = [PROMPT_TEMPLATE.format(instruction=instruction),
                             "Magnified view of the region you requested. Coordinates "
                             "still refer to the full-page screenshot. If you can now read "
                             "the value, report it with finished().", crop]
                else:
                    # Any other action is a no-op here; re-show the page and let it retry.
                    parts = [PROMPT_TEMPLATE.format(instruction=instruction),
                             f"'{name}' does nothing in this read-only task. Read the value "
                             "and report it with finished(), zooming first if needed.", img]
            rec["note"] = rec["note"] or "->".join(rec["path"])
        except Exception as e:
            rec["note"] = f"ERROR: {type(e).__name__}: {e}"[:90]
        rows.append(rec)
        print(f"{args.label:12s} {title[:34]:36s} action={str(rec['action']):9s} "
              f"zoom={'Y' if rec['zoomed'] else 'n'} turns={rec['turns']} "
              f"{'OK' if rec['correct'] else 'WRONG':5s} {rec['note'][:46]}")

    n = len(rows)
    zoomed = sum(r["zoomed"] for r in rows)
    correct = sum(r["correct"] for r in rows)
    zc = sum(r["correct"] for r in rows if r["zoomed"])
    dc = sum(r["correct"] for r in rows if not r["zoomed"])
    print(f"\n--- {args.label} (image {digest}) ---")
    print(f"  zoom_rate       : {zoomed}/{n} ({100 * zoomed / n:.0f}%)   <-- self-direction")
    print(f"  overall correct : {correct}/{n} ({100 * correct / n:.0f}%)")
    print(f"  correct | zoomed: {zc}/{zoomed}" if zoomed else "  correct | zoomed: n/a")
    print(f"  correct | direct: {dc}/{n - zoomed}" if n - zoomed else "  correct | direct: n/a")

    out = f"zoombench_{args.label}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"label": args.label, "image_sha256_16": digest,
                   "zoom_rate": f"{zoomed}/{n}", "correct": f"{correct}/{n}",
                   "rows": rows}, f, indent=2)
    print(f"  written: {out}")


if __name__ == "__main__":
    sys.exit(main())
