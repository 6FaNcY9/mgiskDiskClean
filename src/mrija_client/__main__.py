from __future__ import annotations
import argparse
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _wait_for_server(url: str, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="MrijaArchive client")
    parser.add_argument("--db", type=Path, help="Path to mail_archive.sqlite")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--no-tui", action="store_true", help="Skip Rich TUI")
    args = parser.parse_args()

    from mrija_client.state import AppState, ClientState
    from mrija_client.server import create_app

    state = AppState()

    if args.db:
        if not args.db.exists():
            print(f"ERROR: database not found: {args.db}", file=sys.stderr)
            sys.exit(1)
        from mrija_client.db import MailDB
        state.db = MailDB(args.db)
        state.db_path = args.db
        state.state = ClientState.RUNNING

    app = create_app(state)
    server_url = f"http://{args.bind}:{args.port}"

    if not os.environ.get("MRIJA_API_KEY"):
        import secrets
        key = secrets.token_hex(16)
        os.environ["MRIJA_API_KEY"] = key
        print(f"API key (set MRIJA_API_KEY to reuse): {key}")

    import uvicorn
    config = uvicorn.Config(app, host=args.bind, port=args.port, log_level="warning")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    if not _wait_for_server(server_url):
        print("ERROR: server did not start", file=sys.stderr)
        sys.exit(1)

    webbrowser.open(server_url)

    if args.no_tui:
        print(f"Server running at {server_url}  (Ctrl+C to stop)")
        try:
            t.join()
        except KeyboardInterrupt:
            pass
    else:
        from mrija_client.tui import run_tui
        run_tui(state, server_url)


if __name__ == "__main__":
    main()
