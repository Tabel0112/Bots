#!/usr/bin/env python3
"""Self-contained small-text reading benchmark for a vision LLM.

Generates its own test image deterministically (no repo, no assets, no browser),
so the same script on any machine scores the same pixels -- it prints a SHA256 of
the image so two runs can be proven comparable.

Measures the thing that actually breaks on canvas/DOM-less pages: reading an exact
number rendered at UI scale, with and without magnifying the region first.

    pip install --user pillow httpx
    python3 cluster_bench.py --base-url http://127.0.0.1:8080/v1 --label holo-72b
"""

import argparse
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
# Fixed data: values chosen to include the digit pairs that get confused at small
# scale (1/8, 2/8, 4/9, 5/6, 8/9) and a mix of 2- and 3-digit numbers.
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


def region_for(i):
    y = TOP + i * ROW_H
    return (60, y + 8, 620, y + 26)


def magnify(img, region, target_w=900):
    crop = img.crop(region)
    if crop.width and crop.width < target_w:
        f = min(4, max(2, round(target_w / crop.width)))
        crop = crop.resize((crop.width * f, crop.height * f), Image.LANCZOS)
    return crop


def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ask(client, base_url, model, img, question):
    r = client.post(f"{base_url}/chat/completions", json={
        "model": model, "temperature": 0.0, "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(img)}"}},
        ]}],
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def first_int(t):
    m = re.search(r"\d+", t.replace(",", ""))
    return int(m.group()) if m else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--model", default="vlm")
    p.add_argument("--label", default="model")
    p.add_argument("--save-image", default=None)
    args = p.parse_args()

    img = build_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
    print(f"image sha256[:16] = {digest}   (must match across machines to compare)")
    if args.save_image:
        img.save(args.save_image)

    client = httpx.Client(timeout=600)
    rows = []
    for i, (title, points, _c) in enumerate(STORIES):
        q = (f'This is a news aggregator page. Read the row titled "{title}". '
             f"How many points does it have? Answer with the number only.")
        for view, image in (("full_page", img), ("magnified", magnify(img, region_for(i)))):
            try:
                raw = ask(client, args.base_url.rstrip("/"), args.model, image, q)
                got = first_int(raw)
            except Exception as e:
                raw, got = f"ERROR: {e}", None
            ok = got == points
            rows.append({"title": title, "view": view, "expected": points,
                         "got": got, "correct": ok, "raw": raw[:80]})
            print(f"{args.label:14s} {view:10s} {title[:34]:36s} "
                  f"exp {points:>4} got {str(got):>5} {'OK' if ok else 'WRONG'}")

    print(f"\n--- {args.label} (image {digest}) ---")
    summary = {}
    for view in ("full_page", "magnified"):
        sub = [r for r in rows if r["view"] == view]
        n = sum(r["correct"] for r in sub)
        summary[view] = f"{n}/{len(sub)}"
        print(f"  {view:10s}: {n}/{len(sub)} correct ({100 * n / len(sub):.0f}%)")

    out = f"bench_{args.label}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"label": args.label, "image_sha256_16": digest,
                   "summary": summary, "rows": rows}, f, indent=2)
    print(f"  written: {out}")


if __name__ == "__main__":
    sys.exit(main())
