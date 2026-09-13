from __future__ import annotations

import uvicorn

from .app import create_app

uvicorn.run(create_app(), host="127.0.0.1", port=4173)
