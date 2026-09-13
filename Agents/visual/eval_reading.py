"""Small-text reading eval: how reliably does a VLM read exact on-screen values,
and does magnifying the region help?

Captures one live Hacker News page, reads the DOM **only to build a ground-truth set**
(the model never sees it), then asks the model to read each story's points from pixels
alone -- once from the full-page screenshot, once from a magnified crop of that story's
row. This is the canvas/WebGL situation, where no DOM extraction is available.

Works against any OpenAI-compatible VLM server.

    python eval_reading.py --label ui-tars --base-url http://127.0.0.1:8080/v1
    python eval_reading.py --label holo1.5 --base-url http://127.0.0.1:8081/v1 \
        --reuse runs/eval/page.png --reuse-truth runs/eval/truth.json
"""

import argparse
import base64
import io
import json
import os
import re
import sys

import httpx
from PIL import Image

QUESTION = ("This is a Hacker News page. Read the story titled \"{title}\". "
            "How many points does it have? Answer with the number only.")


def b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def magnify(img, region, target_w=900):
    x0, y0, x1, y1 = region
    crop = img.crop((max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)))
    if crop.width and crop.width < target_w:
        f = min(4, max(2, round(target_w / crop.width)))
        crop = crop.resize((crop.width * f, crop.height * f), Image.LANCZOS)
    return crop


def capture(out_dir, limit, page_zoom=100):
    """Capture a live page plus a DOM-derived ground-truth set."""
    from browser_subagent import load_env
    load_env()
    from browser_subagent.steel_browser import SteelBrowser

    os.makedirs(out_dir, exist_ok=True)
    b = SteelBrowser()
    try:
        b.start()
        b.navigate("https://news.ycombinator.com")
        if page_zoom != 100:
            b._page.evaluate(f"document.body.style.zoom = '{page_zoom}%'")
            b._page.wait_for_timeout(1000)
        rows = b._page.evaluate(
            """(() => [...document.querySelectorAll('.athing')].map(s => {
                const sub = s.nextElementSibling;
                const score = sub && sub.querySelector('.score');
                const a = s.querySelector('.titleline > a');
                const r = a.getBoundingClientRect();
                if (!score) return null;
                return {title: a.innerText,
                        points: parseInt(score.innerText),
                        box: [r.x, r.y, r.right, r.y + 34]};
            }).filter(Boolean))()""")
        shot = b.screenshot_b64()
        img = Image.open(io.BytesIO(base64.b64decode(shot)))
        img.save(os.path.join(out_dir, "page.png"))
        ox, oy = b.view_offset
        truth = []
        for r in rows[:limit]:
            x0, y0, x1, y1 = r["box"]
            if y0 + oy < 0 or y1 + oy > img.height:
                continue  # only rows fully visible in the screenshot
            truth.append({"title": r["title"], "points": r["points"],
                          "region": [int(x0 + ox) - 30, int(y0 + oy),
                                     int(x1 + ox) + 40, int(y1 + oy)]})
        with open(os.path.join(out_dir, "truth.json"), "w", encoding="utf-8") as f:
            json.dump(truth, f, indent=2, ensure_ascii=False)
        print(f"captured {len(truth)} stories with ground truth -> {out_dir}")
        return os.path.join(out_dir, "page.png"), truth
    finally:
        b.stop()


def ask(client, base_url, model, image, question):
    r = client.post(f"{base_url}/chat/completions", json={
        "model": model, "temperature": 0.0, "max_tokens": 64,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64(image)}"}},
        ]}],
    })
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def first_int(text):
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group()) if m else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("UITARS_BASE_URL", "http://127.0.0.1:8080/v1"))
    p.add_argument("--model", default="vlm")
    p.add_argument("--label", default="model")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--out-dir", default=os.path.join("runs", "eval"))
    p.add_argument("--reuse", default=None, help="existing page.png (compare models on identical input)")
    p.add_argument("--reuse-truth", default=None, help="existing truth.json")
    p.add_argument("--page-zoom", type=int, default=100,
                   help="browser zoom %% at capture time (deterministic, no model cooperation)")
    args = p.parse_args()

    if args.reuse:
        page_path = args.reuse
        with open(args.reuse_truth or os.path.join(os.path.dirname(args.reuse), "truth.json"),
                  encoding="utf-8") as f:
            truth = json.load(f)
        print(f"reusing {page_path} ({len(truth)} stories)")
    else:
        page_path, truth = capture(args.out_dir, args.limit, args.page_zoom)

    img = Image.open(page_path)
    client = httpx.Client(timeout=300)
    rows = []
    for t in truth:
        q = QUESTION.format(title=t["title"][:70])
        for view, image in (("full_page", img), ("magnified", magnify(img, t["region"]))):
            try:
                raw = ask(client, args.base_url.rstrip("/"), args.model, image, q)
                got = first_int(raw)
            except Exception as e:
                raw, got = f"ERROR: {e}", None
            ok = got == t["points"]
            rows.append({"model": args.label, "title": t["title"][:50], "view": view,
                         "expected": t["points"], "got": got, "correct": ok, "raw": raw[:80]})
            print(f"{args.label:12s} {view:10s} {t['title'][:38]:40s} "
                  f"exp {t['points']:>4} got {str(got):>5} {'OK' if ok else 'WRONG'}")

    print(f"\n--- {args.label} ---")
    for view in ("full_page", "magnified"):
        sub = [r for r in rows if r["view"] == view]
        if sub:
            n = sum(r["correct"] for r in sub)
            print(f"  {view:10s}: {n}/{len(sub)} correct ({100*n/len(sub):.0f}%)")

    out = os.path.join(os.path.dirname(page_path), f"eval_{args.label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"  written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
