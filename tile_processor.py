#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
tile_processor.py - Obrada GeoTIFF-ova u 256x256 piramidu
Kreiranja VRT-a, rezanje bez resamplinga, piramidizacija s NAME imenima
"""

import logging
import math
import shutil
import time
from pathlib import Path
from osgeo import gdal
import geopandas as gpd
from shapely.geometry import box, Point

from utils import find_geotiff_files, save_checkpoint, load_checkpoint, write_worldfile, check_blank_tile

logger = logging.getLogger("PyramidTool")
gdal.UseExceptions()


class TileProcessor:
    """Klasa za obradu GeoTIFF datoteka u piramidu od 256x256 tile-ova"""

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

        logger.info(f"TileProcessor inicijaliziran:\n  Ulaz: {self.input_dir}\n  Izlaz: {self.output_dir}")

    def stop(self):
        self.should_stop = True
        logger.info("Zahtjev za zaustavljanje obrade")

    def create_vrt(self, progress_callback=None):
        """Kreira VRT od svih GeoTIFF datoteka"""
        logger.info("=== FAZA 1: Stvaranje VRT-a ===")
        tif_files = find_geotiff_files(self.input_dir)
        if not tif_files:
            logger.error("Nema GeoTIFF datoteka za obradu")
            return None

        logger.info(f"Pronađeno {len(tif_files)} GeoTIFF datoteka")

        self.vrt_dir.mkdir(exist_ok=True)
        vrt_file = self.vrt_dir / f"{self.input_dir.name}.vrt"

        try:
            # Postavi opciju da GDAL ne kreira .aux.xml datoteke
            gdal.SetConfigOption('GDAL_DISABLE_READDIR_ON_OPEN', 'YES')

            gdal.BuildVRT(str(vrt_file), [str(f) for f in tif_files],
                          options=gdal.BuildVRTOptions(resolution='highest'))
            if progress_callback:
                progress_callback(100)
            logger.info(f"VRT kreiran: {vrt_file}")
            return vrt_file
        except Exception as e:
            logger.error(f"Greška pri kreiranju VRT-a: {e}")
            return None

    def load_grid(self, grid_path):
        """Učitava shapefile mreže listova"""
        try:
            self.grid_gdf = gpd.read_file(grid_path)
            logger.info(f"Učitano {len(self.grid_gdf)} listova iz mreže")
            return True
        except Exception as e:
            logger.error(f"Greška pri učitavanju mreže: {e}")
            return False

    def get_list_name_for_tile(self, tile_bbox):
        """
        Određuje NAME atribut lista kojem pripada tile.

        Logika:
        1. Provjeri je li centroid tile-a sadržan u nekom listu
        2. Ako je sadržan u više listova, odaberi onaj s najvećim presjekom
        3. Ako nijedan ne sadrži centroid, pronađi listu s najvećim presjekom
        4. Ako nema presjeka, vrati None

        Args:
            tile_bbox: tuple (xmin, ymin, xmax, ymax)

        Returns:
            str: NAME atribut ili None
        """
        if self.grid_gdf is None or len(self.grid_gdf) == 0:
            return None

        xmin, ymin, xmax, ymax = tile_bbox
        tile_centroid = Point((xmin + xmax) / 2, (ymin + ymax) / 2)
        tile_geom = box(xmin, ymin, xmax, ymax)

        # Pronađi sve liste koje sijeku s tile-om
        intersecting_lists = self.grid_gdf[self.grid_gdf.geometry.intersects(tile_geom)]

        if len(intersecting_lists) == 0:
            return None

        # Provjeri koja lista sadrži centroid
        containing_lists = intersecting_lists[intersecting_lists.geometry.contains(tile_centroid)]

        if len(containing_lists) > 0:
            # Ako centroid pripada točno jednoj listi
            if len(containing_lists) == 1:
                return containing_lists.iloc[0]['name']
            # Ako centroid pripada više listova (granica), odaberi onaj s najvećim presjekom
            max_intersection = 0
            best_name = None
            for _, row in containing_lists.iterrows():
                intersection_area = tile_geom.intersection(row.geometry).area
                if intersection_area > max_intersection:
                    max_intersection = intersection_area
                    best_name = row['name']
            return best_name

        # Ako nijedan centroid ne sadrži, pronađi listu s najvećim presjekom
        max_intersection = 0
        best_name = None
        for _, row in intersecting_lists.iterrows():
            intersection_area = tile_geom.intersection(row.geometry).area
            if intersection_area > max_intersection:
                max_intersection = intersection_area
                best_name = row['name']

        return best_name

    def calculate_pyramid_levels(self):
        """
        Izračunava broj razina piramide Z.
        Z = ceil(log2(max(width, height) / 256))

        Returns:
            int: Broj razina (0..Z)
        """
        if not self.vrt_ds:
            return 0

        width = self.vrt_ds.RasterXSize
        height = self.vrt_ds.RasterYSize
        max_dim = max(width, height)

        z = math.ceil(math.log2(max_dim / self.tile_size))
        logger.info(f"Dimenzije mozaika: {width}×{height} px")
        logger.info(f"Izračunati Z (broj razina): {z}")

        return z

    def cut_tiles(self, vrt_file, progress_callback=None, eta_callback=None):
        """
        Rezanja 256×256 tile-ove iz VRT-a sa resamplingom za više razine.
        Kreira sve razine piramide (0, 1, 2, ..., Z).
        Svi tileovi su 256x256 px, sa paddingom transparentnim ako je potrebno.
        """
        logger.info("=== FAZA 2: Rezanje tile-ova ===")

        self.vrt_ds = gdal.Open(str(vrt_file))
        if not self.vrt_ds:
            logger.error("Ne mogu otvoriti VRT")
            return False

        gt = self.vrt_ds.GetGeoTransform()
        vrt_width = self.vrt_ds.RasterXSize
        vrt_height = self.vrt_ds.RasterYSize
        projection = self.vrt_ds.GetProjectionRef()

        pixel_size_x = gt[1]
        pixel_size_y = abs(gt[5])
        min_x = gt[0]
        max_y = gt[3]
        max_x = gt[0] + vrt_width * pixel_size_x
        min_y = max_y - vrt_height * pixel_size_y

        width_geo = max_x - min_x
        height_geo = max_y - min_y

        logger.info(f"VRT geotransform: {gt}")
        logger.info(f"VRT dimenzije: {vrt_width}×{vrt_height} px")

        # Učitaj mrežu listova
        if not self.load_grid(self.grid_shapefile):
            return False

        z_max = self.calculate_pyramid_levels()

        # Izračunaj približan ukupan broj tile-ova
        total_expected_tiles = 0
        level_grids = []
        for z in range(z_max + 1):
            level_downsample = 2 ** z
            tile_geo_width = pixel_size_x * self.tile_size * level_downsample
            tile_geo_height = pixel_size_y * self.tile_size * level_downsample
            tiles_x = math.ceil(width_geo / tile_geo_width)
            tiles_y = math.ceil(height_geo / tile_geo_height)
            level_grids.append((tiles_x, tiles_y))
            total_expected_tiles += tiles_x * tiles_y

        # Spremi checkpoint na početku rezanja
        checkpoint_data = {
            'phase': 'cutting_in_progress',
            'vrt_file': str(vrt_file),
            'input_dir': str(self.input_dir),
            'output_dir': str(self.output_dir),
            'grid_shapefile': str(self.grid_shapefile),
            'tile_format': self.tile_format,
            'z_max': z_max,
            'tiles_created': 0,
            'last_z': -1,
            'last_y': -1,
            'last_x': -1
        }
        save_checkpoint(self.checkpoint_file, checkpoint_data)

        # Kreiraj output direktorije
        self.output_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        processed_tiles = 0
        total_tiles = 0  # stvarni kreirani, ne expected

        # Ako resume
        resume_checkpoint = load_checkpoint(self.checkpoint_file)
        if resume_checkpoint and resume_checkpoint.get('phase') == 'cutting_in_progress':
            processed_tiles = resume_checkpoint.get('tiles_created', 0)
            last_z = resume_checkpoint.get('last_z', -1)
            last_y = resume_checkpoint.get('last_y', -1)
            last_x = resume_checkpoint.get('last_x', -1)
            start_time = time.time() - resume_checkpoint.get('elapsed_time',
                                                             0)  # ako spremamo elapsed, ali za sada reset na resume
        else:
            last_z = -1
            last_y = -1
            last_x = -1

        # Za svaku razinu
        for z in range(z_max + 1):
            if self.should_stop:
                break

            level_downsample = 2 ** z
            tile_geo_width = pixel_size_x * self.tile_size * level_downsample
            tile_geo_height = pixel_size_y * self.tile_size * level_downsample

            tiles_x, tiles_y = level_grids[z]

            logger.info(
                f"Razina {z}: tile size=256px (geo ≈{tile_geo_width:.2f}x{tile_geo_height:.2f}), grid={tiles_x}×{tiles_y}")

            level_dir = self.output_dir / str(z)
            level_dir.mkdir(exist_ok=True, parents=True)

            level_tile_count = 0
            level_tiles_by_name = {}

            if z <= last_z:
                # Preskoči ako već gotov
                continue

            y_start = 0 if z > last_z else (last_y + 1 if last_y >= 0 else 0)

            for y_idx in range(y_start, tiles_y):
                x_start = 0 if z > last_z or y_idx > (last_y if last_y >= 0 else 0) else (
                    last_x + 1 if last_x >= 0 else 0)

                for x_idx in range(x_start, tiles_x):
                    if self.should_stop:
                        break

                    xmin = min_x + x_idx * tile_geo_width
                    ymax = max_y - y_idx * tile_geo_height
                    xmax = min(min_x + (x_idx + 1) * tile_geo_width, max_x)
                    ymin = max(max_y - (y_idx + 1) * tile_geo_height, min_y)

                    tile_bbox = (xmin, ymin, xmax, ymax)
                    list_name = self.get_list_name_for_tile(tile_bbox)

                    if list_name is None:
                        logger.debug(f"Tile ({z}, {x_idx}, {y_idx}) je izvan mreže listova, preskačem")
                        processed_tiles += 1
                        continue

                    # Odredi naziv tile-a ovisno o razini
                    if z <= 2:
                        tile_filename = f"{list_name}-{x_idx}-{y_idx}.{self.tile_format}"
                    else:
                        tile_filename = f"{list_name}.{self.tile_format}"

                    if z >= 3 and tile_filename in level_tiles_by_name:
                        logger.debug(f"Tile {tile_filename} na razini {z} već postoji, preskaču duplikat")
                        processed_tiles += 1
                        continue

                    tile_path = level_dir / tile_filename

                    # Izreži tile pomoću gdal.Warp (sa resamplingom i paddingom)
                    try:
                        # Postavi opcije da se ne kreiraju .aux.xml datoteke
                        gdal.SetConfigOption('GDAL_PAM_ENABLED', 'NO')

                        resample_alg = 'average' if level_downsample > 1 else 'near'

                        creation_opts = [] if self.tile_format == 'png' else ['COMPRESS=NONE']

                        warp_options = gdal.WarpOptions(
                            format='PNG' if self.tile_format == 'png' else 'GTiff',
                            resampleAlg=resample_alg,
                            width=self.tile_size,
                            height=self.tile_size,
                            dstAlpha=True,
                            dstNodata=[0, 0, 0, 0],
                            creationOptions=creation_opts,
                            outputBounds=[xmin, ymin, xmax, ymax]
                        )

                        ds_tile = gdal.Warp(str(tile_path), self.vrt_ds, options=warp_options)

                        if ds_tile:
                            ds_tile = None

                            # Generiraj world file
                            pixel_size_out_x = (xmax - xmin) / self.tile_size
                            pixel_size_out_y = - (ymax - ymin) / self.tile_size
                            write_worldfile(tile_path, pixel_size_out_x, pixel_size_out_y, xmin, ymax,
                                            extension='.pgw' if self.tile_format == 'png' else '.tfw')

                            # Provjeri je li blank i obriši ako jest
                            is_blank = check_blank_tile(tile_path)
                            if is_blank:
                                tile_path.unlink(missing_ok=True)
                                worldfile_ext = '.pgw' if self.tile_format == 'png' else '.tfw'
                                tile_path.with_suffix(worldfile_ext).unlink(missing_ok=True)
                                logger.debug(f"Obrisan prazni tile: {tile_filename}")
                            else:
                                level_tile_count += 1
                                total_tiles += 1
                                if z >= 3:
                                    level_tiles_by_name[tile_filename] = True

                            processed_tiles += 1

                            # Ažuriraj progress i ETA
                            elapsed = time.time() - start_time
                            if processed_tiles > 0 and total_expected_tiles > 0:
                                progress = min(100, int((processed_tiles / total_expected_tiles) * 100))
                                if progress_callback:
                                    progress_callback(progress)

                                eta_sec = (elapsed / processed_tiles) * (total_expected_tiles - processed_tiles)
                                eta_min = int(eta_sec // 60)
                                eta_sec = int(eta_sec % 60)
                                eta_str = f"{eta_min}m {eta_sec}s"
                                if eta_callback:
                                    eta_callback(eta_str)

                            # Spremi checkpoint
                            checkpoint_data = {
                                'phase': 'cutting_in_progress',
                                'vrt_file': str(vrt_file),
                                'input_dir': str(self.input_dir),
                                'output_dir': str(self.output_dir),
                                'grid_shapefile': str(self.grid_shapefile),
                                'tile_format': self.tile_format,
                                'z_max': z_max,
                                'tiles_created': processed_tiles,
                                'last_z': z,
                                'last_y': y_idx,
                                'last_x': x_idx,
                                'elapsed_time': elapsed
                            }
                            save_checkpoint(self.checkpoint_file, checkpoint_data)

                            if not is_blank:
                                logger.debug(f"Rezan tile: {tile_filename} (z={z}, x={x_idx}, y={y_idx})")
                        else:
                            logger.warning(f"Greška pri rezanju tile-a: {tile_filename}")
                            processed_tiles += 1  # i dalje count kao procesiran

                    except Exception as e:
                        logger.error(f"Greška pri rezanju {tile_filename}: {e}")
                        processed_tiles += 1

                if self.should_stop:
                    break

            logger.info(f"Razina {z}: {level_tile_count} tile-ova")

        logger.info(f"Ukupno rezano: {total_tiles} tile-ova")
        self.vrt_ds = None

        if not self.should_stop:
            save_checkpoint(self.checkpoint_file, {
                'phase': 'cutting_completed',
                'vrt_file': str(vrt_file),
                'input_dir': str(self.input_dir),
                'output_dir': str(self.output_dir),
                'grid_shapefile': str(self.grid_shapefile),
                'tile_format': self.tile_format,
                'z_max': z_max
            })

        return True

    def cleanup(self):
        """Čisti privremene datoteke"""
        logger.info("=== FAZA 3: Čišćenje ===")
        try:
            if self.vrt_dir and self.vrt_dir.exists():
                shutil.rmtree(self.vrt_dir)
                logger.info("Obrisan VRT direktorij.")

            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
                logger.info("Checkpoint obrisan.")

        except Exception as e:
            logger.error(f"Greška pri čišćenju: {e}")

    def process_all(self, vrt_progress_cb=None, cut_progress_cb=None,
                    eta_callbacks=None, resume_checkpoint=None):
        """Glavna metoda obrade"""
        try:
            vrt_file = None
            phase = resume_checkpoint.get('phase') if resume_checkpoint else None

            # Ako nema resume_checkpoint, učitaj iz file-a ako postoji
            if resume_checkpoint is None:
                cp = load_checkpoint(self.checkpoint_file)
                if cp:
                    resume_checkpoint = cp
                    phase = resume_checkpoint.get('phase')

            if phase:
                logger.info(f"Nastavak obrade iz faze: '{phase}'")
                vrt_file = Path(resume_checkpoint['vrt_file']) if resume_checkpoint.get('vrt_file') else None

            # FAZA 1: Kreiraj VRT
            if phase not in ['cutting_completed', 'cutting_in_progress']:
                vrt_file = self.create_vrt(vrt_progress_cb)
                if not vrt_file or self.should_stop:
                    return False
            else:
                if vrt_progress_cb:
                    vrt_progress_cb(100)

            # FAZA 2: Rezanje tile-ova
            if phase != 'cutting_completed':
                success = self.cut_tiles(vrt_file, cut_progress_cb,
                                         eta_callbacks.get('cut') if eta_callbacks else None)
                if not success or self.should_stop:
                    return False
            else:
                if cut_progress_cb:
                    cut_progress_cb(100)

            # FAZA 3: Čišćenje
            self.cleanup()

            logger.info("Obrada uspješno završena!")
            return True

        except Exception as e:
            logger.error(f"Greška u process_all: {e}", exc_info=True)
            return False
