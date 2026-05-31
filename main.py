"""
main.py — Entry point for Railway / Render deployment.
Starts a tiny HTTP server on PORT so the platform doesn't kill the process,
then launches the Telegram bot in the same event loop.
"""

import asyncio
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

log = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK - New-API Checkin Bot running")

    def log_message(self, *args):
        pass  # silence access logs


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    log.info(f"Health server on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

    # Start keep-alive HTTP server in a background thread
    t = threading.Thread(target=start_health_server, daemon=True)
    t.start()

    # Start the bot (blocking)
    from bot import main
    main()
