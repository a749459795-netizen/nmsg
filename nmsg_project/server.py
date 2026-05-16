#!/usr/bin/env python3
"""
nmsg Server Entry Point

Usage:
    python server.py [--host HOST] [--port PORT] [--db DB] [--storage STORAGE]
    python server.py --gui          # Launch admin GUI
"""

import sys
import argparse
import signal
import pathlib

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))


def run_cli(args):
    """Headless CLI mode."""
    from src.server.server import NmsgServer

    pathlib.Path(args.storage).mkdir(parents=True, exist_ok=True)

    server = NmsgServer(
        host=args.host,
        port=args.port,
        db_path=args.db,
        storage_root=args.storage,
    )

    def signal_handler(sig, frame):
        print("\nShutting down...")
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print(f"Starting nmsg server on {args.host}:{args.port}")
    print(f"Database: {args.db} | Storage: {args.storage}")
    print("Press Ctrl+C to stop")
    server.start()


def run_gui():
    """GUI admin panel mode."""
    from PyQt6.QtWidgets import QApplication
    from src.server.server_gui import ServerAdminWindow

    app = QApplication(sys.argv)
    app.setApplicationName("nmsg Server")
    app.setOrganizationName("nmsg")

    def sigint_handler():
        app.quit()
    signal.signal(signal.SIGINT, sigint_handler)

    w = ServerAdminWindow()
    w.show()
    sys.exit(app.exec())


def main():
    parser = argparse.ArgumentParser(description="nmsg Server")
    parser.add_argument("--gui", action="store_true", help="Launch admin GUI instead of CLI mode")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=9000, help="Port (default: 9000)")
    parser.add_argument("--db", default="nmsg.db", help="SQLite database path")
    parser.add_argument("--storage", default="storage", help="Storage root directory")
    args = parser.parse_args()

    if args.gui:
        run_gui()
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
