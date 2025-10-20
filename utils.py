#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
utils.py - Enhanced utility functions for GDAL-based raster tiling
Enhanced worldfile handling, checkpointing, blank tile detection, and gdal2tiles integration
"""

import json
import logging
import multiprocessing
import time
from pathlib import Path

import numpy as np
from PIL import Image
from osgeo import gdal, osr

logger = logging.getLogger("PyramidTool")


def find_geotiff_files(input_dir):
    """Pronađi sve GeoTIFF datoteke u direktoriju"""
    input_path = Path(input_dir)
    tif_files = list(input_path.glob("*.tif")) + list(input_path.glob("*.tiff"))
    logger.info(f"Found {len(tif_files)} GeoTIFF files in {input_dir}")
    return sorted(tif_files)


def get_cpu_count():
    """Pronađi broj CPU jezgri"""
    return multiprocessing.cpu_count()


def create_aligned_vrt(input_files, output_vrt, pixel_size=None, align_to_grid=True):
    """Kreiraj VRT aligned na 256x256 pixel grid"""
    try:
        geotransforms = []
        projections = []
        datasets = []

        logger.info(f"Analyzing {len(input_files)} GeoTIFF files for VRT creation")

        for tif_file in input_files:
            ds = gdal.Open(str(tif_file))
            if ds:
                gt = ds.GetGeoTransform()
                proj = ds.GetProjectionRef()
                geotransforms.append(gt)
                projections.append(proj)
                datasets.append(ds)
            else:
                logger.warning(f"Could not open {tif_file.name}")

        if not geotransforms:
            logger.error("No valid GeoTIFF files found")
            return False

        if pixel_size is None:
            pixel_sizes_x = [abs(gt[1]) for gt in geotransforms]
            pixel_sizes_y = [abs(gt[5]) for gt in geotransforms]
            pixel_size = min(min(pixel_sizes_x), min(pixel_sizes_y))
            logger.info(f"Calculated highest resolution: {pixel_size} units/pixel")

        all_xmins, all_ymaxs, all_xmaxs, all_ymins = [], [], [], []

        for i, (gt, ds) in enumerate(zip(geotransforms, datasets)):
            xmin = gt[0]
            ymax = gt[3]
            xmax = gt[0] + ds.RasterXSize * gt[1]
            ymin = gt[3] - ds.RasterYSize * abs(gt[5])

            all_xmins.append(xmin)
            all_ymaxs.append(ymax)
            all_xmaxs.append(xmax)
            all_ymins.append(ymin)

        for ds in datasets:
            ds = None

        min_x = min(all_xmins)
        max_x = max(all_xmaxs)
        min_y = min(all_ymins)
        max_y = max(all_ymaxs)

        logger.info(f"Overall bounds: ({min_x:.2f}, {min_y:.2f}, {max_x:.2f}, {max_y:.2f})")

        if align_to_grid:
            logger.info("Aligning bounds to 256x256 pixel grid")
            width_geo = max_x - min_x
            height_geo = max_y - min_y

            tiles_x = int(np.ceil(width_geo / (pixel_size * 256)))
            tiles_y = int(np.ceil(height_geo / (pixel_size * 256)))

            aligned_width = tiles_x * pixel_size * 256
            aligned_height = tiles_y * pixel_size * 256

            width_diff = aligned_width - width_geo
            height_diff = aligned_height - height_geo

            min_x -= width_diff / 2
            max_x += width_diff / 2
            min_y -= height_diff / 2
            max_y += height_diff / 2

            logger.info(f"Aligned bounds: ({min_x:.2f}, {min_y:.2f}, {max_x:.2f}, {max_y:.2f})")
            logger.info(f"Grid dimensions: {tiles_x}×{tiles_y} tiles")

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3765)
        output_crs_wkt = srs.ExportToWkt()

        vrt_options = gdal.BuildVRTOptions(
            resolution='user',
            outputBounds=[min_x, min_y, max_x, max_y],
            xRes=pixel_size,
            yRes=pixel_size,
            resampleAlg='near',
            outputSRS=output_crs_wkt
        )

        logger.info(f"Building VRT with resolution {pixel_size} units/pixel and target SRS EPSG:3765")
        gdal.BuildVRT(str(output_vrt), [str(f) for f in input_files], options=vrt_options)

        if Path(output_vrt).exists():
            logger.info(f"Successfully created aligned VRT: {output_vrt}")
            vrt_ds = gdal.Open(str(output_vrt))
            if vrt_ds:
                logger.info(f"VRT dimensions: {vrt_ds.RasterXSize}×{vrt_ds.RasterYSize} pixels")
                logger.info(f"VRT geotransform: {vrt_ds.GetGeoTransform()}")
                vrt_ds = None
            return True
        else:
            logger.error("VRT file was not created")
            return False

    except Exception as e:
        logger.error(f"Error creating aligned VRT: {e}", exc_info=True)
        return False


def extract_tile_stem(tif_path):
    """Ekstrahiraj stem iz GeoTIFF naziva"""
    return Path(tif_path).stem


def generate_tile_name(stem, z, x, y, format_ext):
    """Generiraj naziv tile-a: stem-z-x-y.ext"""
    return f"{stem}-{z}-{x}-{y}.{format_ext}"


def write_worldfile(tile_path, pixel_size_x, pixel_size_y, ulx, uly, output_format='png'):
    """Napiši worldfile (.pgw, .jgw ili .tfw)"""
    try:
        # Odredi ekstenziju worldfile-a
        if output_format == 'png':
            wf_ext = '.pgw'
        elif output_format == 'jpg':
            wf_ext = '.jgw'
        else:
            wf_ext = '.pgw'

        worldfile_path = tile_path.with_suffix(wf_ext)

        with open(worldfile_path, 'w') as f:
            f.write(f"{pixel_size_x}\n")
            f.write("0.0\n")
            f.write("0.0\n")
            f.write(f"{pixel_size_y}\n")
            f.write(f"{ulx}\n")
            f.write(f"{uly}\n")

        logger.debug(f"Worldfile written: {worldfile_path.name}")
        return True

    except Exception as e:
        logger.error(f"Error writing worldfile for {tile_path.name}: {e}")
        return False


def check_blank_tile(tile_data):
    """Provjeri je li tile prazan"""
    try:
        if len(tile_data.shape) == 3:
            if tile_data.shape[2] == 4:
                alpha = tile_data[:, :, 3]
                if np.all(alpha < 10):
                    return True
                rgb_masked = tile_data[:, :, :3][alpha > 10]
            else:
                rgb_masked = tile_data.reshape(-1, 3)
        else:
            rgb_masked = tile_data.reshape(-1, 1)

        if len(rgb_masked) == 0:
            return True

        variance = np.var(rgb_masked)
        if variance < 5:
            return True

        unique_colors = np.unique(rgb_masked, axis=0)
        if len(unique_colors) <= 10:
            return True

        return False

    except Exception as e:
        logger.warning(f"Error checking blank tile: {e}")
        return False


def run_gdal2tiles_optimized(vrt_path, output_dir, tile_format='png', current_zoom=None,
                             total_zoom_levels=None, progress_callback=None,
                             start_time=None, eta_callback=None, tile_stem=None):
    """Optimiziran tiling s proper naming i worldfile generiranjem"""
    try:
        logger.info("=== Direct GDAL Tiling ===")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        gdal_z = current_zoom if current_zoom is not None else 0
        logger.info(f"Tiling zoom level: {gdal_z}")

        ds = gdal.Open(str(vrt_path))
        if not ds:
            logger.error(f"Cannot open VRT: {vrt_path}")
            return False

        width = ds.RasterXSize
        height = ds.RasterYSize
        gt = ds.GetGeoTransform()
        logger.info(f"VRT dimensions: {width}×{height} px")

        gdal.SetConfigOption('GDAL_PAM_ENABLED', 'NO')

        tile_size = 256
        tiles_x = (width + tile_size - 1) // tile_size
        tiles_y = (height + tile_size - 1) // tile_size
        total_tiles = tiles_x * tiles_y

        logger.info(f"Generating {tiles_x}×{tiles_y} tiles ({total_tiles} total)")

        tile_count = 0
        blank_tiles = 0
        start_tile_time = time.time()

        for tile_y in range(tiles_y):
            for tile_x in range(tiles_x):
                src_xoff = tile_x * tile_size
                src_yoff = tile_y * tile_size
                src_xsize = min(tile_size, width - src_xoff)
                src_ysize = min(tile_size, height - src_yoff)

                if src_xsize <= 0 or src_ysize <= 0:
                    continue

                # Generiraj tile filename: stem-z-x-y.ext
                tile_filename = generate_tile_name(tile_stem, gdal_z, tile_x, tile_y, tile_format)
                tile_path = output_path / tile_filename

                # Koristi gdal.Translate() za tile generiranje
                translate_options = gdal.TranslateOptions(
                    srcWin=[src_xoff, src_yoff, src_xsize, src_ysize],
                    format='PNG' if tile_format == 'png' else ('JPEG' if tile_format == 'jpg' else 'GTiff'),
                    creationOptions=['ZLEVEL=9'] if tile_format == 'png' else
                    (['QUALITY=95'] if tile_format == 'jpg' else ['COMPRESS=DEFLATE']),
                    resampleAlg='near'
                )

                try:
                    out_ds = gdal.Translate(str(tile_path), ds, options=translate_options)

                    if out_ds is None:
                        logger.error(f"Failed to create tile: {tile_path}")
                        continue

                    # Provjeri je li tile prazan
                    band = out_ds.GetRasterBand(1)
                    tile_data = band.ReadAsArray()

                    if check_blank_tile(tile_data):
                        out_ds = None
                        tile_path.unlink(missing_ok=True)
                        # Obriši i worldfile ako postoji
                        for wf_ext in ['.pgw', '.jgw', '.tfw']:
                            wf_path = tile_path.with_suffix(wf_ext)
                            wf_path.unlink(missing_ok=True)
                        blank_tiles += 1
                        logger.debug(f"Skipped blank tile: {tile_filename}")
                    else:
                        out_ds.FlushCache()
                        out_ds = None

                        # Generiraj worldfile
                        ulx = gt[0] + src_xoff * gt[1]
                        uly = gt[3] + src_yoff * gt[5]
                        pixel_size_x = gt[1]
                        pixel_size_y = gt[5]

                        write_worldfile(tile_path, pixel_size_x, pixel_size_y, ulx, uly, tile_format)

                        tile_count += 1

                        # ETA calculation
                        if tile_count % 100 == 0 and start_time:
                            elapsed = time.time() - start_time
                            tiles_per_sec = tile_count / elapsed if elapsed > 0 else 0

                            if tiles_per_sec > 0:
                                remaining_tiles = total_tiles - tile_count
                                eta_seconds = remaining_tiles / tiles_per_sec
                                eta_str = format_eta(eta_seconds)
                                logger.info(f"Generated {tile_count}/{total_tiles} tiles - ETA: {eta_str}")
                                if eta_callback:
                                    eta_callback(eta_str)

                            if progress_callback:
                                progress = int(100 * tile_count / total_tiles)
                                progress_callback(progress)

                except Exception as e:
                    logger.warning(f"Failed to create tile {tile_x}-{tile_y}: {e}")
                    continue

        ds = None

        logger.info(f"Successfully generated {tile_count} tiles ({blank_tiles} blanks skipped)")

        if progress_callback:
            progress_callback(100)

        return tile_count > 0

    except Exception as e:
        logger.error(f"Error in GDAL tiling: {e}", exc_info=True)
        return False


def downsample_tiles(source_dir, target_dir, tile_format='png', tile_stem=None,
                     source_z=None, target_z=None, progress_callback=None,
                     start_time=None, eta_callback=None):
    """Generiraj niži zoom nivo downsamplanjem 4 tile-a iz višeg nivoa"""
    try:
        source_path = Path(source_dir)
        target_path = Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Downsampling from z={source_z} to z={target_z}")

        # Pronađi sve tile-ove na source nivou
        source_tiles = list(source_path.glob(f"*.{tile_format}"))

        if not source_tiles:
            logger.warning(f"No source tiles found in {source_dir}")
            return False

        # Parsiranje tile koordinata iz naziva: stem-z-x-y.ext
        tile_coords = {}
        for tile_path in source_tiles:
            try:
                parts = tile_path.stem.split('-')
                if len(parts) >= 4:
                    x = int(parts[-2])
                    y = int(parts[-1])
                    tile_coords[(x, y)] = tile_path
            except (ValueError, IndexError):
                logger.debug(f"Skipping tile with unexpected name: {tile_path.name}")
                continue

        if not tile_coords:
            logger.error("No valid tiles found for downsampling")
            return False

        # Odredi raspon tile koordinata na target nivou
        max_x = max(x for x, y in tile_coords.keys())
        max_y = max(y for x, y in tile_coords.keys())

        target_max_x = max_x // 2
        target_max_y = max_y // 2

        total_target_tiles = (target_max_x + 1) * (target_max_y + 1)
        logger.info(f"Will generate up to {total_target_tiles} downsampled tiles")

        tile_count = 0
        blank_tiles = 0
        start_downsample_time = time.time()

        # Generiraj svaki target tile kombiniranjem 4 source tile-a
        for target_y in range(target_max_y + 1):
            for target_x in range(target_max_x + 1):
                # 4 source tile-a koje trebamo kombinirati
                source_coords = [
                    (target_x * 2, target_y * 2),
                    (target_x * 2 + 1, target_y * 2),
                    (target_x * 2, target_y * 2 + 1),
                    (target_x * 2 + 1, target_y * 2 + 1)
                ]

                # Provjeri postoje li source tile-ovi
                source_tiles_exist = [coord in tile_coords for coord in source_coords]

                if not any(source_tiles_exist):
                    # Nijedan source tile ne postoji, preskoči
                    continue

                # Kreiraj target tile
                target_tile_name = generate_tile_name(tile_stem, target_z, target_x, target_y, tile_format)
                target_tile_path = target_path / target_tile_name

                try:
                    # Kreiraj praznu sliku 256x256
                    if tile_format == 'png':
                        combined = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
                    else:
                        combined = Image.new('RGB', (256, 256), (0, 0, 0))

                    # Učitaj i smanjuj svaki source tile
                    quad_positions = [(0, 0), (128, 0), (0, 128), (128, 128)]
                    has_content = False

                    for (src_x, src_y), (paste_x, paste_y) in zip(source_coords, quad_positions):
                        if (src_x, src_y) in tile_coords:
                            src_tile_path = tile_coords[(src_x, src_y)]
                            try:
                                src_img = Image.open(src_tile_path)
                                # Smanji na 128x128
                                src_img_resized = src_img.resize((128, 128), Image.LANCZOS)
                                combined.paste(src_img_resized, (paste_x, paste_y))
                                has_content = True
                            except Exception as e:
                                logger.debug(f"Error loading source tile {src_tile_path.name}: {e}")

                    if not has_content:
                        blank_tiles += 1
                        continue

                    # Provjeri je li rezultat prazan
                    img_array = np.array(combined)
                    if check_blank_tile(img_array):
                        blank_tiles += 1
                        continue

                    # Spremi tile
                    if tile_format == 'png':
                        combined.save(target_tile_path, 'PNG', optimize=True)
                    elif tile_format == 'jpg':
                        combined.save(target_tile_path, 'JPEG', quality=95)
                    else:
                        combined.save(target_tile_path)

                    tile_count += 1

                    # Generiraj worldfile (uzmi iz prvog source tile-a i prilagodi)
                    first_source = tile_coords.get(source_coords[0])
                    if first_source:
                        for wf_ext in ['.pgw', '.jgw', '.tfw']:
                            source_wf = first_source.with_suffix(wf_ext)
                            if source_wf.exists():
                                try:
                                    with open(source_wf, 'r') as f:
                                        lines = f.readlines()

                                    # Pixel size se udvostručuje (jer kombiniramo 4 tile-a)
                                    pixel_x = float(lines[0].strip()) * 2
                                    pixel_y = float(lines[3].strip()) * 2
                                    ulx = float(lines[4].strip())
                                    uly = float(lines[5].strip())

                                    target_wf = target_tile_path.with_suffix(wf_ext)
                                    with open(target_wf, 'w') as f:
                                        f.write(f"{pixel_x}\n")
                                        f.write("0.0\n")
                                        f.write("0.0\n")
                                        f.write(f"{pixel_y}\n")
                                        f.write(f"{ulx}\n")
                                        f.write(f"{uly}\n")

                                    break
                                except Exception as e:
                                    logger.debug(f"Error copying worldfile: {e}")

                    # ETA calculation
                    if tile_count % 100 == 0 and start_time:
                        elapsed = time.time() - start_time
                        tiles_per_sec = tile_count / (time.time() - start_downsample_time) if (
                                                                                                          time.time() - start_downsample_time) > 0 else 0

                        if tiles_per_sec > 0:
                            remaining_tiles = total_target_tiles - tile_count
                            eta_seconds = remaining_tiles / tiles_per_sec
                            eta_str = format_eta(eta_seconds)
                            logger.info(f"Downsampled {tile_count}/{total_target_tiles} tiles - ETA: {eta_str}")
                            if eta_callback:
                                eta_callback(eta_str)

                        if progress_callback:
                            progress = int(100 * tile_count / total_target_tiles)
                            progress_callback(progress)

                except Exception as e:
                    logger.warning(f"Failed to create downsampled tile {target_x}-{target_y}: {e}")
                    continue

        logger.info(f"Successfully downsampled {tile_count} tiles ({blank_tiles} blanks skipped)")

        if progress_callback:
            progress_callback(100)

        return tile_count > 0

    except Exception as e:
        logger.error(f"Error in downsampling: {e}", exc_info=True)
        return False


def format_eta(seconds):
    """Formatiraj ETA vrijeme"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"


def save_checkpoint(checkpoint_file, data):
    """Spremi checkpoint u JSON"""
    try:
        with open(checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug(f"Checkpoint saved: {checkpoint_file}")
    except Exception as e:
        logger.error(f"Error saving checkpoint: {e}")


def load_checkpoint(checkpoint_file):
    """Učitaj checkpoint iz JSON-a"""
    checkpoint_path = Path(checkpoint_file)

    if not checkpoint_path.exists():
        logger.info("Checkpoint file does not exist")
        return None

    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        logger.info(f"Checkpoint loaded: {checkpoint_file}")
        return data
    except Exception as e:
        logger.error(f"Error loading checkpoint: {e}")
        return None
