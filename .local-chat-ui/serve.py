"""Throwaway local chat UI for the BTP Guardian agent.

Serves index.html and proxies /a2a -> the agent's A2A JSON-RPC endpoint,
streaming SSE straight back to the browser (avoids any CORS issues).

Usage:
    python .local-chat-ui/serve.py            # UI on :8000, agent on :8080
    python .local-chat-ui/serve.py 8001 8080  # UI port, agent port

Then open http://localhost:<ui-port> in a browser.
Requires the agent to be running: `uv run run_local.py 8080`
"""
import http.server
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
UI_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
AGENT_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
AGENT_URL = f"http://localhost:{AGENT_PORT}/"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_POST(self):
        if self.path != "/a2a":
            self.send_error(404)
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = urllib.request.Request(
            AGENT_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                # A2A streaming needs the client to accept SSE.
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=300)
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(
                f"Could not reach agent at {AGENT_URL} ({e}). "
                "Is it running? `uv run run_local.py "
                f"{AGENT_PORT}`".encode()
            )
            return

        # Stream the response (SSE or JSON) straight through to the browser.
        self.send_response(upstream.status)
        ctype = upstream.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                chunk = upstream.read(1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except BrokenPipeError:
            pass

    def log_message(self, *args):
        pass  # quiet


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", UI_PORT), Handler) as httpd:
        print(f"Chat UI:  http://localhost:{UI_PORT}")
        print(f"Proxying to agent at {AGENT_URL}")
        print("Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
