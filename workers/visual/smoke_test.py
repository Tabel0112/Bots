import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_subagent import load_env
load_env()

from browser_subagent.steel_browser import SteelBrowser

b = SteelBrowser()
try:
    s = b.start()
    print("session:", s.id)
    print("viewer:", s.session_viewer_url)
    nav = b.navigate("https://news.ycombinator.com")
    print("navigated:", nav)
    shot = b.screenshot_b64()
    print("screenshot bytes(b64):", len(shot))
    import base64, io
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(shot)))
    print("screenshot size:", img.size)
    el = b.element_at(200, 100)
    print("element at (200,100):", el)
    text, shot2 = b.execute("scroll", {"coordinate": [640, 400], "scroll_direction": "down", "scroll_amount": 3})
    print("scroll:", text, "post-shot:", bool(shot2))
    text, _ = b.execute("key", {"text": "Page_Down"})
    print("key press:", text)
    print("network log entries:", len(b.network_log))
    if b.network_log:
        print("first:", b.network_log[0]["method"], b.network_log[0]["url"][:80], b.network_log[0]["status"])
    print("SMOKE TEST PASSED")
finally:
    b.stop()
