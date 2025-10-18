#!/usr/bin/env python3
# -*- coding: utf-8 -*-

__author__ = 'David'

"""
gui.py - PyQt5 GUI za Pyramid Tool
Gumbi, progress barovi, odabir direktorija, statusne poruke
"""

import logging
import time
from pathlib import Path
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QProgressBar, QFileDialog, QMessageBox,
    QComboBox, QTextEdit, QGroupBox, QApplication
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QIcon, QTextCursor

from tile_processor import TileProcessor
from utils import load_checkpoint

logger = logging.getLogger("PyramidTool")


class ProcessingThread(QThread):
    """Thread za izvršavanje obrade u pozadini"""

    vrt_progress = pyqtSignal(int)
    cut_progress = pyqtSignal(int)
    status_message = pyqtSignal(str)
    eta = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, processor, checkpoint=None):
        super().__init__()
        self.processor = processor
        self.checkpoint = checkpoint

    def run(self):
        """Izvršava obradu"""
        try:
            self.status_message.emit("Započinjem obradu...")

            def eta_cb(eta_str):
                self.eta.emit(eta_str)

            eta_callbacks = {
                'cut': eta_cb
            }

            success = self.processor.process_all(
                vrt_progress_cb=self.vrt_progress.emit,
                cut_progress_cb=self.cut_progress.emit,
                eta_callbacks=eta_callbacks,
                resume_checkpoint=self.checkpoint
            )

            if self.processor.should_stop:
                self.status_message.emit("Obrada pauzirana od strane korisnika.")
                self.finished.emit(False)
            elif success:
                self.status_message.emit("Obrada uspješno završena!")
                self.finished.emit(True)
            else:
                self.status_message.emit("Obrada prekinuta zbog greške.")
                self.finished.emit(False)

        except Exception as e:
            logger.error(f"Greška u processing threadu: {e}", exc_info=True)
            self.status_message.emit(f"Kritična greška: {e}")
            self.finished.emit(False)


class PyramidToolGUI(QMainWindow):
    """Glavno GUI sučelje"""

    def __init__(self):
        super().__init__()
        self.processor = None
        self.processing_thread = None
        self.is_processing = False
        self.is_paused = False
        self.is_exiting = False
        self.init_ui()

    def init_ui(self):
        """Inicijalizacija GUI komponenti"""
        self.setWindowTitle("Pyramid Tool - GeoTIFF Processor")
        self.setGeometry(100, 100, 800, 600)

        icon_path = Path(__file__).parent / "icon" / "protok_ico.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # --- Sekcija za direktorije ---
        dir_group = QGroupBox("Direktoriji")
        dir_layout = QVBoxLayout()
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Ulazni folder:"))
        self.input_dir_edit = QLineEdit()
        self.input_dir_edit.setPlaceholderText("Odaberite folder s GeoTIFF datotekama")
        input_layout.addWidget(self.input_dir_edit)
        self.input_browse_btn = QPushButton("Browse...")
        self.input_browse_btn.clicked.connect(self.browse_input_dir)
        input_layout.addWidget(self.input_browse_btn)
        dir_layout.addLayout(input_layout)

        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("Izlazni folder:"))
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("Automatski: ime_ulaza-PYR")
        output_layout.addWidget(self.output_dir_edit)
        self.output_browse_btn = QPushButton("Browse...")
        self.output_browse_btn.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.output_browse_btn)
        dir_layout.addLayout(output_layout)

        grid_layout = QHBoxLayout()
        grid_layout.addWidget(QLabel("Mreža listova (shapefile):"))
        self.grid_edit = QLineEdit()
        self.grid_edit.setPlaceholderText("ml4096.shp")
        grid_layout.addWidget(self.grid_edit)
        self.grid_browse_btn = QPushButton("Browse...")
        self.grid_browse_btn.clicked.connect(self.browse_grid)
        grid_layout.addWidget(self.grid_browse_btn)
        dir_layout.addLayout(grid_layout)

        dir_group.setLayout(dir_layout)
        main_layout.addWidget(dir_group)

        # --- Sekcija za postavke ---
        settings_group = QGroupBox("Postavke")
        settings_layout = QHBoxLayout()
        settings_layout.addWidget(QLabel("Format tile-ova:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG (.png + .pgw)", "TIFF (.tif)"])
        settings_layout.addWidget(self.format_combo)
        settings_layout.addStretch()
        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)

        # --- Sekcija za progress ---
        progress_group = QGroupBox("Napredak")
        progress_layout = QVBoxLayout()
        progress_layout.addWidget(QLabel("1. Stvaranje VRT-a:"))
        self.vrt_progress_bar = QProgressBar()
        progress_layout.addWidget(self.vrt_progress_bar)

        progress_layout.addWidget(QLabel("2. Rezanje tile-ova:"))
        self.cut_progress_bar = QProgressBar()
        progress_layout.addWidget(self.cut_progress_bar)

        eta_layout = QHBoxLayout()
        eta_layout.addWidget(QLabel("Preostalo vrijeme:"))
        self.eta_label = QLabel("N/A")
        eta_layout.addWidget(self.eta_label)
        progress_layout.addLayout(eta_layout)

        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)

        # --- Sekcija za status ---
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(120)
        status_layout.addWidget(self.status_text)
        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)

        # --- Gumbi za kontrolu ---
        button_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(lambda: self.start_processing(checkpoint=None))
        self.start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        button_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_processing)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold;")
        button_layout.addWidget(self.stop_btn)

        self.continue_btn = QPushButton("Continue")
        self.continue_btn.clicked.connect(self.continue_processing)
        self.continue_btn.setEnabled(False)
        self.continue_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
        button_layout.addWidget(self.continue_btn)

        self.exit_btn = QPushButton("Exit")
        self.exit_btn.clicked.connect(self.close)
        self.exit_btn.setEnabled(True)
        self.exit_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        button_layout.addWidget(self.exit_btn)
        main_layout.addLayout(button_layout)

        self.check_checkpoint()
        logger.info("GUI inicijaliziran")

    def browse_input_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Odaberite ulazni folder")
        if dir_path:
            self.input_dir_edit.setText(dir_path)
            input_path = Path(dir_path)
            output_path = input_path.parent / f"{input_path.name}-PYR"
            self.output_dir_edit.setText(str(output_path))

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Odaberite izlazni folder")
        if dir_path:
            self.output_dir_edit.setText(dir_path)

    def browse_grid(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Odaberite shapefile s mrežom listova", filter="Shapefile (*.shp)")
        if file_path:
            self.grid_edit.setText(file_path)

    def check_checkpoint(self):
        checkpoint = load_checkpoint(".pyramid_checkpoint.json")
        if checkpoint:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Question)
            msg_box.setWindowTitle("Pronađen checkpoint")
            msg_box.setText("Pronađena je nedovršena obrada. Želite li nastaviti?")
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            msg_box.setDefaultButton(QMessageBox.Yes)
            msg_box.button(QMessageBox.Yes).setText("Da")
            msg_box.button(QMessageBox.No).setText("Ne")
            reply = msg_box.exec_()

            if reply == QMessageBox.Yes:
                self.continue_btn.setEnabled(True)
                self.start_btn.setEnabled(False)
                self.input_dir_edit.setText(checkpoint.get('input_dir', ''))
                self.output_dir_edit.setText(checkpoint.get('output_dir', ''))
                self.grid_edit.setText(checkpoint.get('grid_shapefile', ''))
                self.set_inputs_enabled(False)
                self.add_status("Checkpoint učitan. Kliknite 'Continue' za nastavak.")
            else:
                Path(".pyramid_checkpoint.json").unlink(missing_ok=True)
                self.add_status("Checkpoint obrisan na zahtjev korisnika.")

    def start_processing(self, checkpoint=None):
        if checkpoint:
            input_dir = checkpoint.get('input_dir')
            output_dir = checkpoint.get('output_dir')
            grid_path = checkpoint.get('grid_shapefile')
            tile_format = checkpoint.get('tile_format')
        else:
            input_dir = self.input_dir_edit.text().strip()
            output_dir = self.output_dir_edit.text().strip()
            grid_path = self.grid_edit.text().strip()

            if not all([input_dir, output_dir, grid_path]):
                QMessageBox.warning(self, "Greška", "Odaberite sve potrebne datoteke i direktorije!")
                return

            if not Path(grid_path).exists():
                QMessageBox.critical(self, "Greška", f"Mreža listova nije pronađena:\n{grid_path}")
                return

            tile_format = 'png' if 'PNG' in self.format_combo.currentText() else 'tif'

        self.processor = TileProcessor(input_dir, output_dir, grid_path, tile_format)
        if not checkpoint:
            self.reset_ui_state()

        self.processing_thread = ProcessingThread(self.processor, checkpoint)
        self.connect_thread_signals()
        self.processing_thread.start()

        self.set_processing_state(True)
        self.add_status("Obrada pokrenuta...")
        logger.info("Obrada pokrenuta")

    def stop_processing(self):
        if self.processor:
            self.processor.stop()
            self.is_paused = True
            self.stop_btn.setEnabled(False)
            self.add_status("Zaustavljanje u tijeku... Pričekajte završetak trenutnog koraka.")
            logger.info("Zahtjev za zaustavljanje")
            QApplication.processEvents()

    def continue_processing(self):
        checkpoint = load_checkpoint(".pyramid_checkpoint.json")
        if not checkpoint:
            QMessageBox.warning(self, "Greška", "Checkpoint datoteka nije pronađena!")
            return

        # Resetuj pauziranu zastavu
        self.is_paused = False
        self.add_status("Nastavak obrade s checkpointa...")

        # Kreiraj processor s datotekama iz checkpointa
        input_dir = checkpoint.get('input_dir')
        output_dir = checkpoint.get('output_dir')
        grid_path = checkpoint.get('grid_shapefile')
        tile_format = checkpoint.get('tile_format', 'png')

        self.processor = TileProcessor(input_dir, output_dir, grid_path, tile_format)
        self.processing_thread = ProcessingThread(self.processor, checkpoint)
        self.connect_thread_signals()
        self.processing_thread.start()

        self.set_processing_state(True)
        logger.info("Obrada nastavljena s checkpointa")

    def on_processing_finished(self, success):
        self.is_processing = False
        if success:
            QMessageBox.information(self, "Završeno", "Obrada je uspješno završena!")
            self.reset_ui_state()
            self.set_inputs_enabled(True)
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
        else:
            if self.is_paused:
                self.add_status("Obrada je pauzirana. Kliknite 'Continue' za nastavak.")
                self.continue_btn.setEnabled(True)
                self.start_btn.setEnabled(False)
                self.stop_btn.setEnabled(False)
            elif self.is_exiting:
                self.add_status("Aplikacija se zatvara po zahtjevu korisnika.")
            else:
                QMessageBox.critical(self, "Greška", "Došlo je do greške tijekom obrade.")
                self.reset_ui_state()
                self.set_inputs_enabled(True)
                self.start_btn.setEnabled(True)

        if not self.is_paused:
            self.is_paused = False
        logger.info("Obrada završena")

    def add_status(self, message):
        self.status_text.append(f"[{time.strftime('%H:%M:%S')}] {message}")
        self.status_text.moveCursor(QTextCursor.End)

    def update_eta(self, eta_str):
        self.eta_label.setText(eta_str)

    def closeEvent(self, event):
        if self.is_processing:
            reply = QMessageBox.question(self, "Obrada u tijeku",
                "Obrada je u tijeku. Prekidom će se spremiti napredak. Želite li izaći?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.is_exiting = True
            if self.processor:
                self.processor.stop()
            if self.processing_thread:
                self.processing_thread.wait(10000)
                if self.processing_thread.isRunning():
                    self.processing_thread.terminate()
        event.accept()

    def set_processing_state(self, is_processing):
        self.is_processing = is_processing
        self.start_btn.setEnabled(not is_processing)
        self.stop_btn.setEnabled(is_processing)
        self.continue_btn.setEnabled(False)
        self.set_inputs_enabled(not is_processing)

    def set_inputs_enabled(self, enabled):
        self.input_dir_edit.setEnabled(enabled)
        self.output_dir_edit.setEnabled(enabled)
        self.grid_edit.setEnabled(enabled)
        self.input_browse_btn.setEnabled(enabled)
        self.output_browse_btn.setEnabled(enabled)
        self.grid_browse_btn.setEnabled(enabled)
        self.format_combo.setEnabled(enabled)

    def reset_ui_state(self):
        self.vrt_progress_bar.setValue(0)
        self.cut_progress_bar.setValue(0)
        self.eta_label.setText("N/A")

    def connect_thread_signals(self):
        self.processing_thread.vrt_progress.connect(self.vrt_progress_bar.setValue)
        self.processing_thread.cut_progress.connect(self.cut_progress_bar.setValue)
        self.processing_thread.status_message.connect(self.add_status)
        self.processing_thread.eta.connect(self.update_eta)
        self.processing_thread.finished.connect(self.on_processing_finished)
