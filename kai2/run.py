#!/usr/bin/env python3
"""KAISPOT Voucher Management - start the server.

Double-click start.bat on Windows, or run:  python run.py
"""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

if sys.version_info < (3, 9):
    print(f"Python 3.9 or newer is needed. This is {sys.version.split()[0]}.")
    raise SystemExit(1)

MISSING = []
for module, package in (("flask", "Flask"), ("pypdf", "pypdf")):
    try:
        __import__(module)
    except ImportError:
        MISSING.append(package)
if MISSING:
    print(f"\n  Missing: {', '.join(MISSING)}\n")
    print("  Run this once, in this folder:\n")
    print("      pip install -r requirements.txt\n")
    raise SystemExit(1)

from backend import seed
from backend.app import create_app
from backend.config import HOST, HOSTED, PORT


def lan_address() -> str:
    """The address other machines on the office WiFi should use. Found by
    asking the OS which interface it would use; no packet is sent, so this
    works with no internet."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def banner(address: str, fresh: bool) -> None:
    line = "=" * 64
    print(f"\n{line}\n  KAISPOT  -  Voucher Management  v3.0\n{line}")
    print(f"\n  In this office        http://localhost:{PORT}")
    print(f"  For agents' phones    http://{address}:{PORT}")
    if fresh:
        print("\n  First run. Sign in with:")
        print("      username   admin")
        print("      password   admin")
        print("  You will be asked to set your own password straight away.")
    print("\n  For agents to reach this before they log in to the hotspot,")
    print(f"  add {address} to the MikroTik walled garden:")
    print(f"      /ip hotspot walled-garden ip")
    print(f"      add action=accept dst-address={address} comment=\"KAISPOT\"")
    print("\n  Leave this window open. Press Ctrl+C to stop.")
    print(f"\n{line}\n")


def main() -> None:
    fresh = seed.bootstrap()
    if "--demo" in sys.argv:
        seed.demo_data()
        print("Sample agents added.")

    app = create_app()
    if HOSTED:
        print(f"KAISPOT starting on port {PORT}")
        if fresh:
            print("First run: sign in with admin / admin and change it at once.")
    else:
        banner(lan_address(), fresh)

    if "--no-browser" not in sys.argv and not HOSTED:
        # A headless server has no browser, and webbrowser.open raises there.
        def open_browser() -> None:
            try:
                webbrowser.open(f"http://localhost:{PORT}")
            except Exception:
                pass
        threading.Timer(1.2, open_browser).start()

    try:
        from waitress import serve
        serve(app, host=HOST, port=PORT, threads=8, ident="KAISPOT")
    except ImportError:
        app.run(host=HOST, port=PORT, threaded=True, debug=False,
                use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Your data is in data/kaispot.db\n")
