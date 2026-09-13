import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from browser_subagent.uitars_agent import extract_thought, parse_action, smart_resize

tests = [
    "Thought: I need to click the search box.\nAction: click(start_box='<|box_start|>(432,88)<|box_end|>')",
    "Thought: typing\nAction: type(content='hello world\\n')",
    "Action: scroll(start_box='(640,400)', direction='down')",
    "Action: hotkey(key='ctrl a')",
    "Action: drag(start_box='(100,200)', end_box='(300,400)')",
    "Action: finished(content='Title: Example, Points: 42')",
    "Action: click(point='<point>500 300</point>')",
    "Action: open_url(url='https://example.com')",
    "Action: wait()",
]
for t in tests:
    name, kw = parse_action(t)
    print(name, kw)

assert parse_action(tests[1])[1]["content"] == "hello world\n"
assert parse_action(tests[5])[1]["content"] == "Title: Example, Points: 42"
print("smart_resize 800x1280 ->", smart_resize(800, 1280))
print("thought:", extract_thought(tests[0]))
print("PARSER TESTS PASSED")
