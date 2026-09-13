import os
from pathlib import Path

import pytest

from Agents.browser_worker.config import Settings, load_sites
from Agents.browser_worker.demo.run import sample_request


@pytest.fixture
def request_data():
    return sample_request()


@pytest.fixture
def sites():
    return load_sites()


@pytest.fixture
def local_settings():
    executable = os.getenv("WORKER_BROWSER_EXECUTABLE")
    if not executable:
        executable = next(
            (
                p
                for p in [
                    "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
                if Path(p).exists()
            ),
            None,
        )
    # None selects Playwright's installed Chromium (including Linux CI).
    return Settings(
        browser="local",
        browser_executable=executable,
        browser_timeout_seconds=3.0,
        model_timeout_seconds=2.0,
    )
