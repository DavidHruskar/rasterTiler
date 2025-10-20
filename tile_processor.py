#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
tile_processor.py - Enhanced GeoTIFF pyramid tiling using gdal2tiles.py
Creates VRT mosaics, generates pyramid tiles with proper naming and resampling
"""

import logging
import math
import shutil
import time
from pathlib import Path

from osgeo import gdal

from utils import (
    find_geotiff_files, save_checkpoint, load_checkpoint,
    create_aligned_vrt, run_gdal2tiles_optimized, extract_tile_stem,
    downsample_tiles
)

logger = logging.getLogger("PyramidTool")
gdal.UseExceptions()


class TileProcessor:
    """Procesira GeoTIFF datoteke u pyramid tile-ove"""

    def __init__(self, input_dir, output_dir, grid_shapefile, tile_format='png'):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.grid_shapefile = Path(grid_shapefile)
        self.tile_format = tile_format.lower()
        self.vrt_dir = self.input_dir.parent / f"{self.input_dir.name}-VRT"
        self.checkpoint_file = Path('.pyramid_checkpoint.json')
        self.should_stop = False
        self.tile_size = 256
        self.vrt_ds = None
        self.grid_gdf = None
        self.tile_stems = []
        self.start_time = None

        logger.info(f"TileProcessor initialized:\n  Input: {self.input_dir}\n  Output: {self.output_dir}\n  Format: {self.tile_format}")

    def stop(self):
        """Zaustavi obradu"""
        self.should_stop = True
        logger.info("Stop request received")

    def create_vrt(self, progress_callback=None):
        """Kreiraj VRT iz svih GeoTIFF datoteka"""
        logger.info("=== PHASE 1: Creating VRT ===")
        tif_files = find_geotiff_files(self.input_dir)
        if not tif_files:
            logger.error("No GeoTIFF files found for processing")
            return None

        logger.info(f"Found {len(tif_files)} GeoTIFF files")

        self.tile_stems = [extract_tile_stem(f) for f in tif_files]
        logger.info(f"Tile stems: {self.tile_stems[:5]}...")

        self.vrt_dir.mkdir(exist_ok=True)
        vrt_file = self.vrt_dir / f"{self.input_dir.name}.vrt"

        try:
            success = create_aligned_vrt(
                tif_files,
                vrt_file,
                pixel_size=None,
                align_to_grid=True
            )

            if success:
                if progress_callback:
                    progress_callback(100)
                logger.info(f"VRT created: {vrt_file}")
                return vrt_file
            else:
                logger.error("Failed to create aligned VRT")
                return None

        except Exception as e:
            logger.error(f"Error creating VRT: {e}")
            return None

    def calculate_pyramid_levels(self):
        """Izračunaj broj pyramid nivoa"""
        if not self.vrt_ds:
            return 0

        width = self.vrt_ds.RasterXSize
        height = self.vrt_ds.RasterYSize
        max_dim = max(width, height)

        z = math.ceil(math.log2(max_dim / self.tile_size))

        logger.info(f"Mosaic dimensions: {width}×{height} px")
        logger.info(f"Calculated z_max: {z}")

        return z

    def generate_tiles_with_gdal2tiles(self, vrt_file, progress_callback=None, eta_callback=None):
        """Generiraj tile-ove"""
        logger.info("=== PHASE 2: Generating tiles ===")

        self.vrt_ds = gdal.Open(str(vrt_file))
        if not self.vrt_ds:
            logger.error("Cannot open VRT")
            return False

        z_max = self.calculate_pyramid_levels()

        self.output_dir.mkdir(parents=True, exist_ok=True)

        checkpoint_data = {
            'phase': 'tiling_in_progress',
            'vrt_file': str(vrt_file),
            'input_dir': str(self.input_dir),
            'output_dir': str(self.output_dir),
            'grid_shapefile': str(self.grid_shapefile),
            'tile_format': self.tile_format,
            'z_max': z_max,
            'tiles_created': 0,
            'last_z': -1
        }
        save_checkpoint(self.checkpoint_file, checkpoint_data)

        self.start_time = time.time()

        # Generiraj od z_max (puna rezolucija) prema z=0 (overview)
        # folder_z predstavlja folder strukturu (0 = najviša rezolucija)
        # gdal_z predstavlja pyramid nivo (z_max = najviša rezolucija)
        for folder_z in range(z_max + 1):
            if self.should_stop:
                break

            gdal_z = z_max - folder_z
            logger.info(f"Processing folder: {folder_z} (pyramid level: {gdal_z})")

            level_dir = self.output_dir / str(folder_z)
            level_dir.mkdir(exist_ok=True)

            # Koristi prvi tile stem za naming
            tile_stem = self.tile_stems[0] if self.tile_stems else "tile"

            if folder_z == 0:
                # Najviši nivo - generiraj direktno iz VRT-a
                logger.info(f"Generating tiles from VRT at pyramid level z={gdal_z}")

                success = run_gdal2tiles_optimized(
                    vrt_file,
                    level_dir,
                    tile_format=self.tile_format,
                    current_zoom=gdal_z,
                    total_zoom_levels=z_max + 1,
                    progress_callback=lambda p: self._update_progress(p, folder_z, z_max, progress_callback),
                    start_time=self.start_time,
                    eta_callback=eta_callback,
                    tile_stem=tile_stem
                )

                if not success:
                    logger.error(f"Failed to generate tiles for folder {folder_z}")
                    return False

            else:
                # Ostali nivoi - generiraj downsamplanjem iz prethodnog nivoa
                prev_folder_z = folder_z - 1
                prev_level_dir = self.output_dir / str(prev_folder_z)

                if not prev_level_dir.exists():
                    logger.error(f"Previous level directory does not exist: {prev_level_dir}")
                    return False

                logger.info(f"Downsampling from folder {prev_folder_z} to folder {folder_z}")

                success = downsample_tiles(
                    prev_level_dir,
                    level_dir,
                    tile_format=self.tile_format,
                    tile_stem=tile_stem,
                    source_z=z_max - prev_folder_z,
                    target_z=gdal_z,
                    progress_callback=lambda p: self._update_progress(p, folder_z, z_max, progress_callback),
                    start_time=self.start_time,
                    eta_callback=eta_callback
                )

                if not success:
                    logger.warning(f"No tiles generated for folder {folder_z} (this may be normal if source had no tiles)")

            # Verifikacija
            tile_files = list(level_dir.glob(f"*.{self.tile_format}"))
            logger.info(f"Verified {len(tile_files)} tiles created for folder {folder_z}")

            # Update checkpoint
            checkpoint_data.update({
                'last_z': folder_z,
                'tiles_created': folder_z + 1,
                'elapsed_time': time.time() - self.start_time
            })
            save_checkpoint(self.checkpoint_file, checkpoint_data)

            logger.info(f"Completed folder {folder_z}")

            if progress_callback:
                overall_progress = int(100 * (folder_z + 1) / (z_max + 1))
                progress_callback(overall_progress)

        if not self.should_stop:
            save_checkpoint(self.checkpoint_file, {
                'phase': 'tiling_completed',
                'vrt_file': str(vrt_file),
                'input_dir': str(self.input_dir),
                'output_dir': str(self.output_dir),
                'grid_shapefile': str(self.grid_shapefile),
                'tile_format': self.tile_format,
                'z_max': z_max
            })

        logger.info("Tile generation completed successfully")
        return True

    def _update_progress(self, level_progress, current_folder_z, z_max, progress_callback):
        """Ažuriraj progress"""
        if progress_callback:
            level_weight = 100.0 / (z_max + 1)
            completed_progress = current_folder_z * level_weight
            current_level_progress = level_progress * (level_weight / 100.0)
            overall_progress = int(completed_progress + current_level_progress)
            progress_callback(min(100, overall_progress))

    def cleanup(self):
        """Očisti privremene datoteke"""
        logger.info("=== PHASE 3: Cleanup ===")
        try:
            if self.vrt_dir and self.vrt_dir.exists():
                shutil.rmtree(self.vrt_dir)
                logger.info("VRT directory removed")

            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
                logger.info("Checkpoint removed")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def process_all(self, vrt_progress_cb=None, cut_progress_cb=None,
                    eta_callbacks=None, resume_checkpoint=None):
        """Glavna obrada"""
        try:
            vrt_file = None
            phase = resume_checkpoint.get('phase') if resume_checkpoint else None

            if resume_checkpoint is None:
                cp = load_checkpoint(self.checkpoint_file)
                if cp:
                    resume_checkpoint = cp
                    phase = resume_checkpoint.get('phase')

            if phase:
                logger.info(f"Resuming from phase: '{phase}'")
                vrt_file = Path(resume_checkpoint['vrt_file']) if resume_checkpoint.get('vrt_file') else None

            # PHASE 1: Create VRT
            if phase not in ['tiling_completed', 'tiling_in_progress']:
                vrt_file = self.create_vrt(vrt_progress_cb)
                if not vrt_file or self.should_stop:
                    return False
            else:
                if vrt_progress_cb:
                    vrt_progress_cb(100)

            # PHASE 2: Generate tiles
            if phase != 'tiling_completed':
                success = self.generate_tiles_with_gdal2tiles(
                    vrt_file,
                    cut_progress_cb,
                    eta_callbacks.get('cut') if eta_callbacks else None
                )
                if not success or self.should_stop:
                    return False
            else:
                if cut_progress_cb:
                    cut_progress_cb(100)

            # PHASE 3: Cleanup
            self.cleanup()

            logger.info("Processing completed successfully!")
            return True

        except Exception as e:
            logger.error(f"Error in process_all: {e}", exc_info=True)
            return False
