#!/usr/bin/env python3
"""
nmsg Client Entry Point
"""

import sys
import os
import pathlib

# Add src to path
ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
from src.client.gui import NmsgMainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("nmsg")
    app.setOrganizationName("nmsg")

    w = NmsgMainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
