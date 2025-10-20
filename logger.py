#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
logger.py - Enhanced logging setup for GDAL-based raster tiling
Configures logging to file and console with proper formatting
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


def setup_logger(name="PyramidTool", log_dir="logs"):
    """Postavlja logger s zapisivanjem u datoteku i konzolu"""

    log_path = Path(log_dir)
    log_path.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"pyramid_tool_{timestamp}.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info(f"Logger inicijaliziran. Log datoteka: {log_file}")

    return logger
