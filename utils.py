#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
utils.py - Pomoćne funkcije
Pronalaženje slika, čitanje worldfile-ova, checkpointing, provjera praznih pločica
"""

import json
import logging
from pathlib import Path
from PIL import Image
import numpy as np

logger = logging.getLogger("PyramidTool")


def find_geotiff_files(input_dir):
    """
    Pronalazi sve GeoTIFF datoteke u direktoriju

    Args:
        input_dir: Putanja do direktorija

    Returns:
        list: Lista Path objekata
    """
    input_path = Path(input_dir)
    tif_files = list(input_path.glob("*.tif")) + list(input_path.glob("*.tiff"))
    logger.info(f"Pronađeno {len(tif_files)} GeoTIFF datoteka u {input_dir}")
    return sorted(tif_files)


def read_worldfile(tif_path):
    """
    Čita worldfile (.tfw ili .tifw) za GeoTIFF

    Args:
        tif_path: Putanja do GeoTIFF datoteke

    Returns:
        dict: Rječnik s parametrima transformacije ili None
    """
    tif_path = Path(tif_path)

    # Pokušaj pronaći worldfile
    tfw_path = tif_path.with_suffix('.tfw')
    if not tfw_path.exists():
        tfw_path = tif_path.with_suffix('.tifw')

    if not tfw_path.exists():
        logger.warning(f"Worldfile nije pronađen za {tif_path.name}")
        return None

    try:
        with open(tfw_path, 'r') as f:
            lines = [float(line.strip()) for line in f.readlines() if line.strip()]

        if len(lines) != 6:
            logger.warning(f"Neispravan worldfile {tfw_path.name}")
            return None

        return {
            'pixel_size_x': lines[0],
            'rotation_y': lines[1],
            'rotation_x': lines[2],
            'pixel_size_y': lines[3],
            'upper_left_x': lines[4],
            'upper_left_y': lines[5]
        }
    except Exception as e:
        logger.error(f"Greška pri čitanju worldfile-a {tfw_path.name}: {e}")
        return None


def write_worldfile(output_path, pixel_size_x, pixel_size_y, ulx, uly, extension='.pgw', crs_wkt=None):
    """
    Zapisuje worldfile s podacima transformacije.

    Args:
        output_path: Path objekt izlazne datoteke (npr. .png ili .jpg)
        pixel_size_x: X veličina piksela (dx)
        pixel_size_y: Y veličina piksela (dy)
        ulx: Gornja lijeva X koordinata (ULX)
        uly: Gornja lijeva Y koordinata (ULY)
        extension: Ekstenzija worldfile-a (.pgw ili .jgw)
        crs_wkt: WKT (Well-Known Text) string projekcije
    """
    worldfile_path = output_path.with_suffix(extension)
    try:
        with open(worldfile_path, 'w') as f:
            f.write(f"{pixel_size_x}\n")
            f.write("0.0\n")  # Rotacija X
            f.write("0.0\n")  # Rotacija Y
            f.write(f"{pixel_size_y}\n")
            f.write(f"{ulx}\n")
            f.write(f"{uly}\n")
        logger.debug(f"Worldfile zapisan: {worldfile_path.name}")

        # Opcionalno: Zapiši PRJ datoteku s CRS-om (za QGIS)
        if crs_wkt:
            prj_path = output_path.with_suffix('.prj')
            with open(prj_path, 'w') as f:
                f.write(crs_wkt)
            logger.debug(f"PRJ datoteka zapisan: {prj_path.name}")

    except Exception as e:
        logger.error(f"Greška pri zapisivanju worldfile-a za {output_path.name}: {e}")
        return False


def save_checkpoint(checkpoint_file, data):
    """
    Sprema checkpoint u JSON datoteku

    Args:
        checkpoint_file: Putanja do checkpoint datoteke
        data: Rječnik s podacima za spremanje
    """
    try:
        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug(f"Checkpoint spremljen: {checkpoint_file}")
    except Exception as e:
        logger.error(f"Greška pri spremanju checkpointa: {e}")


def load_checkpoint(checkpoint_file):
    """
    Učitava checkpoint iz JSON datoteke

    Args:
        checkpoint_file: Putanja do checkpoint datoteke

    Returns:
        dict: Učitani podaci ili None
    """
    checkpoint_path = Path(checkpoint_file)

    if not checkpoint_path.exists():
        logger.info("Checkpoint datoteka ne postoji")
        return None

    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"Checkpoint učitan: {checkpoint_file}")
        return data
    except Exception as e:
        logger.error(f"Greška pri učitavanju checkpointa: {e}")
        return None


def check_blank_tile(image_path, threshold=10, variance_threshold=5):
    """
    Provjerava je li pločica prazna (potpuno bijela, crna ili jednobojna)

    Args:
        image_path: Putanja do slike
        threshold: Prag za broj jedinstvenih boja
        variance_threshold: Prag za varijancu piksela

    Returns:
        bool: True ako je pločica prazna, False inače
    """
    try:
        with Image.open(image_path) as img:
            # Konvertiraj u RGBA ako je potrebno
            if img.mode not in ['RGB', 'RGBA']:
                img = img.convert('RGBA')
            elif img.mode == 'RGB':
                img = img.convert('RGBA')

            img_array = np.array(img)

            rgb = img_array[..., :3]
            alpha = img_array[..., 3]

            # Ako su svi alfa 0, prazno
            if np.all(alpha == 0):
                logger.debug(f"Prazna pločica (svi alfa=0): {Path(image_path).name}")
                return True

            # Uzmi samo piksele gdje alfa > 0
            masked_rgb = rgb[alpha > 0]

            if len(masked_rgb) == 0:
                logger.debug(f"Prazna pločica (nema neprozirnih piksela): {Path(image_path).name}")
                return True

            # Provjeri varijancu na maskiranim pikselima
            variance = np.var(masked_rgb)
            if variance < variance_threshold:
                logger.debug(f"Prazna pločica (niska varijanca): {Path(image_path).name}")
                return True

            # Provjeri broj jedinstvenih boja na maskiranim pikselima
            unique_colors = np.unique(masked_rgb, axis=0)

            if len(unique_colors) <= threshold:
                logger.debug(f"Prazna pločica (malo boja): {Path(image_path).name}, boje: {len(unique_colors)}")
                return True

            return False

    except Exception as e:
        logger.warning(f"Greška pri provjeri prazne pločice {image_path}: {e}")
        # U slučaju greške, pretpostavi da nije prazna
        return False


def is_blank_array(array, threshold=10, variance_threshold=5):
    """
    Provjerava je li numpy array (slika) prazan

    Args:
        array: Numpy array slike
        threshold: Prag za broj jedinstvenih boja
        variance_threshold: Prag za varijancu

    Returns:
        bool: True ako je slika prazna
    """
    try:
        # Provjeri varijancu
        variance = np.var(array)
        if variance < variance_threshold:
            logger.debug("Prazan array (niska varijanca)")
            return True

        # Provjeri broj jedinstvenih boja
        if len(array.shape) == 3:
            pixels = array.reshape(-1, array.shape[-1])
        else:
            pixels = array.reshape(-1, 1)

        unique_colors = np.unique(pixels, axis=0)

        if len(unique_colors) <= threshold:
            logger.debug(f"Prazan array (malo boja: {len(unique_colors)})")
            return True

        return False

    except Exception as e:
        logger.warning(f"Greška pri provjeri praznog array-a: {e}")
        return False
