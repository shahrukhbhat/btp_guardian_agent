"""Local chat UI for the BTP Guardian agent.

Serves index.html and proxies /a2a -> the agent's A2A JSON-RPC endpoint,
streaming SSE straight back to the browser (avoids any CORS issues).

The UI always runs locally; the *agent* it talks to is configurable:
  - local agent  (default): http://localhost:8080/
  - deployed CF agent:      https://btp-guardian-agent.cfapps.eu10.hana.ondemand.com/

Usage:
    # local agent (unchanged)
    python .local-chat-ui/serve.py                 # UI :8000, agent :8080
    python .local-chat-ui/serve.py 8001 8080       # UI port, agent port

    # deployed CF agent (no local agent needed)
    python .local-chat-ui/serve.py 8000 --target https://btp-guardian-agent.cfapps.eu10.hana.ondemand.com/
    AGENT_URL=https://btp-guardian-agent.cfapps.eu10.hana.ondemand.com/ python .local-chat-ui/serve.py 8000

Target precedence: --target/--agent-url flag > positional URL > AGENT_URL env >
http://localhost:<agent-port>/.
Then open http://localhost:<ui-port> in a browser.
"""
import http.server
import json
import os
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def _resolve_config(argv):
    """Parse argv into (ui_port, agent_url, is_local).

    Accepts the legacy positional form (`<ui-port> <agent-port>`) plus a
    `--target`/`--agent-url` flag, a positional URL, or the AGENT_URL env var.
    """
    ui_port = 8000
    agent_port = 8080
    target = None

    positionals = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--target", "--agent-url"):
            i += 1
            if i >= len(argv):
                sys.exit(f"{arg} requires a URL argument")
            target = argv[i]
        elif arg.startswith("--target=") or arg.startswith("--agent-url="):
            target = arg.split("=", 1)[1]
        elif arg.startswith(("http://", "https://")):
            target = arg
        else:
            positionals.append(arg)
        i += 1

    if len(positionals) >= 1:
        ui_port = int(positionals[0])
    if len(positionals) >= 2:
        agent_port = int(positionals[1])

    if target is None:
        target = os.environ.get("AGENT_URL")

    if target:
        agent_url = target if target.endswith("/") else target + "/"
    else:
        agent_url = f"http://localhost:{agent_port}/"

    is_local = agent_url.startswith(("http://localhost", "http://127.0.0.1"))
    return ui_port, agent_url, is_local


UI_PORT, AGENT_URL, IS_LOCAL = _resolve_config(sys.argv[1:])


def _unreachable_hint(err):
    if IS_LOCAL:
        return (
            f"Could not reach the local agent at {AGENT_URL} ({err}). "
            "Is it running? Start it with `uv run run_local.py 8080`."
        )
    return (
        f"Could not reach the deployed agent at {AGENT_URL} ({err}). "
        "Check the URL, your network/VPN, and that the CF app is running "
        "(`cf apps`). Cold starts can take a few seconds — try again."
    )


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def end_headers(self):
        # Never let the browser cache the local UI — always serve fresh HTML/JS.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def do_GET(self):
        if self.path == "/whoami":
            payload = json.dumps({"target": AGENT_URL, "isLocal": IS_LOCAL}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

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
        except urllib.error.HTTPError as e:
            # Agent reachable but returned an error — pass the body through so
            # the UI can show the real message (CF may return HTML/JSON errors).
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "text/plain"))
            self.end_headers()
            try:
                self.wfile.write(e.read())
            except (BrokenPipeError, OSError):
                pass
            return
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(_unreachable_hint(e).encode())
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
        print(f"Proxying to agent at {AGENT_URL} ({'local' if IS_LOCAL else 'deployed'})")
        print("Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
