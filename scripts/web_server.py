"""
scripts/web_server.py
=====================
Backward-compatible facade for the modularized web server layer.
All symbols are re-exported from scripts/server/ sub-modules.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

try:
    from .server import EngineWebServer, _EngineRequestHandler, _QuietThreadingHTTPServer
except (ImportError, ModuleNotFoundError):
    try:
        from spd_analysis_engine.scripts.server import (
            EngineWebServer, _EngineRequestHandler, _QuietThreadingHTTPServer,
        )
    except (ImportError, ModuleNotFoundError):
        from scripts.server import (
            EngineWebServer, _EngineRequestHandler, _QuietThreadingHTTPServer,
        )

__all__ = [
    "EngineWebServer",
    "_EngineRequestHandler",
    "_QuietThreadingHTTPServer",
]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SPD Analysis Engine Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host IP to bind to")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    args = parser.parse_args()

    engine_root = Path(__file__).resolve().parent.parent
    server = EngineWebServer(engine_root, host=args.host, port=args.port)
    actual_port = server.start(background=True)
    print(f"EngineWebServer listening on http://{args.host}:{actual_port}")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        server.shutdown()
