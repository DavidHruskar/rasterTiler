#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
main.py - Glavna skripta za pokretanje aplikacije
Upravlja GUI-jem i postavlja splash ekran
"""

import sys
import time
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QSplashScreen
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtCore import Qt

from gui import PyramidToolGUI
from logger import setup_logger


def main():
    """Glavna funkcija aplikacije"""

    app = QApplication(sys.argv)
    app.setApplicationName("Pyramid Tool")

    icon_path = Path(__file__).parent / "icon" / "protok_ico.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    splash_path = Path(__file__).parent / "icon" / "protok_logo.png"
    splash = None
    if splash_path.exists():
        splash_pixmap = QPixmap(str(splash_path))
        splash = QSplashScreen(splash_pixmap, Qt.WindowStaysOnTopHint)
        splash.show()
        app.processEvents()
        time.sleep(2)

    logger = setup_logger()
    logger.info("=" * 80)
    logger.info("Pokrenuta nova instanca Pyramid Tool aplikacije")
    logger.info("=" * 80)

    window = PyramidToolGUI()
    window.show()

    if splash:
        splash.finish(window)

    exit_code = app.exec_()

    logger.info("Zatvorena instanca aplikacije")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
