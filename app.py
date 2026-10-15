from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QThread, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from video_merger import (
    QUALITY_PROFILES, VIDEO_EXTENSIONS, ClipInfo, MergeCancelled, MergeResult,
    MergeSettings, format_bytes, format_duration, inspect_clip, merge_videos,
    suggested_output_path,
)


APP_NAME = "UnisciVideo"


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def ffmpeg_path() -> Path:
    bundled = resource_path("bin/ffmpeg.exe")
    if bundled.exists():
        return bundled
    local = Path(__file__).resolve().parent / "bin" / "ffmpeg.exe"
    return local


class DropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(220)
        self.setToolTip("Trascina qui due o più clip video")

    def dragEnterEvent(self, event) -> None:
        valid = event.mimeData().hasUrls() and any(
            Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTENSIONS for url in event.mimeData().urls()
        )
        event.acceptProposedAction() if valid else event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [
            url.toLocalFile() for url in event.mimeData().urls()
            if Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTENSIONS
        ]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#7a8799"))
            painter.drawText(
                self.viewport().rect().adjusted(18, 18, -18, -18),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Trascina qui le clip nell’ordine desiderato\noppure usa “Aggiungi video”",
            )


class MergeWorker(QObject):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal(bool)

    def __init__(self, clips: list[ClipInfo], output: Path, settings: MergeSettings) -> None:
        super().__init__()
        self.clips = clips
        self.output = output
        self.settings = settings
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        cancelled = False
        try:
            result = merge_videos(
                self.clips, self.output, self.settings, ffmpeg_path(),
                lambda value, message: self.progress.emit(value, message), self.cancel_event,
            )
            self.completed.emit(result)
        except MergeCancelled:
            cancelled = True
        except Exception as exc:
            self.failed.emit(str(exc))
        self.finished.emit(cancelled)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.clips: list[ClipInfo] = []
        self.output_path: Path | None = None
        self.result: MergeResult | None = None
        self.worker: MergeWorker | None = None
        self.thread: QThread | None = None
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(940, 720)
        self.resize(1020, 800)
        icon = QIcon(str(resource_path("assets/dog_video.ico")))
        self.setWindowIcon(icon if not icon.isNull() else make_fallback_icon())
        self._build_ui()
        self._apply_style()
        self._update_summary()

    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(30, 22, 30, 24)
        outer.setSpacing(16)

        title_row = QHBoxLayout()
        mascot_label = QLabel()
        mascot_label.setObjectName("mascot")
        mascot_label.setFixedSize(94, 94)
        mascot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mascot = QPixmap(str(resource_path("assets/dog_video.png")))
        if mascot.isNull():
            mascot_label.setText("▶")
        else:
            mascot_label.setPixmap(mascot.scaled(92, 92, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        title_box = QVBoxLayout()
        title = QLabel("UnisciVideo")
        title.setObjectName("title")
        subtitle = QLabel("Le tue clip, in ordine, in un unico video pronto da condividere")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        title_row.addWidget(mascot_label)
        title_row.addLayout(title_box)
        title_row.addStretch()
        outer.addLayout(title_row)

        clips_card, clips_layout = self._card("1. Scegli e ordina le clip")
        buttons = QHBoxLayout()
        self.add_button = QPushButton("+  Aggiungi video")
        self.add_button.setObjectName("primarySmall")
        self.up_button = QPushButton("↑  Su")
        self.down_button = QPushButton("↓  Giù")
        self.remove_button = QPushButton("Rimuovi")
        self.clear_button = QPushButton("Svuota")
        for button in (self.add_button, self.up_button, self.down_button, self.remove_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch()
        clips_layout.addLayout(buttons)
        self.clip_list = DropList()
        clips_layout.addWidget(self.clip_list)
        self.summary = QLabel("Nessuna clip selezionata")
        self.summary.setObjectName("muted")
        clips_layout.addWidget(self.summary)
        outer.addWidget(clips_card, 1)

        settings_grid = QGridLayout()
        settings_grid.setHorizontalSpacing(18)
        quality_card, quality_layout = self._card("2. Qualità del video")
        self.quality_combo = QComboBox()
        for key, profile in QUALITY_PROFILES.items():
            self.quality_combo.addItem(profile["name"], key)
        self.quality_combo.setCurrentIndex(self.quality_combo.findData("balanced"))
        quality_layout.addWidget(self.quality_combo)
        quality_note = QLabel("Tutte le clip vengono rese compatibili tra loro. Audio stereo AAC e video H.264 funzionano su quasi tutti i dispositivi.")
        quality_note.setWordWrap(True)
        quality_note.setObjectName("infoBox")
        quality_layout.addWidget(quality_note)
        settings_grid.addWidget(quality_card, 0, 0)

        format_card, format_layout = self._card("3. Formato e destinazione")
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItem("Come la prima clip", "source")
        self.resolution_combo.addItem("Full HD orizzontale · 1920×1080", "1080p")
        self.resolution_combo.addItem("HD orizzontale · 1280×720", "720p")
        self.resolution_combo.addItem("Verticale · 1080×1920", "vertical")
        self.resolution_combo.addItem("Quadrato · 1080×1080", "square")
        format_layout.addWidget(self.resolution_combo)
        pause_row = QHBoxLayout()
        self.black_gap_check = QCheckBox("Inserisci una pausa nera tra le clip")
        self.black_gap_spin = QDoubleSpinBox()
        self.black_gap_spin.setRange(0.1, 10.0)
        self.black_gap_spin.setSingleStep(0.5)
        self.black_gap_spin.setDecimals(1)
        self.black_gap_spin.setValue(1.0)
        self.black_gap_spin.setSuffix(" s")
        self.black_gap_spin.setEnabled(False)
        pause_row.addWidget(self.black_gap_check)
        pause_row.addStretch()
        pause_row.addWidget(self.black_gap_spin)
        format_layout.addLayout(pause_row)
        output_row = QHBoxLayout()
        self.output_label = QLabel("La destinazione verrà proposta dopo aver aggiunto le clip")
        self.output_label.setObjectName("muted")
        self.output_label.setWordWrap(True)
        self.output_button = QPushButton("Scegli file…")
        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(self.output_button)
        format_layout.addLayout(output_row)
        settings_grid.addWidget(format_card, 0, 1)
        settings_grid.setColumnStretch(0, 1)
        settings_grid.setColumnStretch(1, 1)
        outer.addLayout(settings_grid)

        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(18, 14, 18, 14)
        actions = QHBoxLayout()
        self.status_label = QLabel("Pronto")
        self.status_label.setObjectName("status")
        self.open_folder_button = QPushButton("Apri cartella risultato")
        self.open_folder_button.hide()
        self.cancel_button = QPushButton("Annulla")
        self.cancel_button.hide()
        self.start_button = QPushButton("Unisci video")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(180)
        actions.addWidget(self.status_label, 1)
        actions.addWidget(self.open_folder_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.start_button)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        bottom_layout.addLayout(actions)
        bottom_layout.addWidget(self.progress_bar)
        outer.addWidget(bottom)
        self.setCentralWidget(root)

        self.add_button.clicked.connect(self._choose_files)
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.remove_button.clicked.connect(self._remove_selected)
        self.clear_button.clicked.connect(self._clear)
        self.clip_list.files_dropped.connect(self._add_files)
        self.output_button.clicked.connect(self._choose_output)
        self.black_gap_check.toggled.connect(self.black_gap_spin.setEnabled)
        self.start_button.clicked.connect(self._start)
        self.cancel_button.clicked.connect(self._cancel)
        self.open_folder_button.clicked.connect(self._open_folder)

    def _card(self, heading: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 16, 18, 17)
        layout.setSpacing(12)
        title = QLabel(heading)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        return frame, layout

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f3f6fb; color: #1f2937; font-family: "Segoe UI"; font-size: 10.5pt; }
            QLabel { background: transparent; }
            QLabel#title { font-size: 23pt; font-weight: 700; color: #172033; }
            QLabel#subtitle, QLabel#muted { color: #667085; }
            QLabel#mascot { background: transparent; }
            QFrame#card { background: white; border: 1px solid #dfe5ef; border-radius: 12px; }
            QLabel#sectionTitle { font-size: 12pt; font-weight: 650; color: #172033; }
            QLabel#infoBox { background: #eff6ff; color: #1e4d91; border: 1px solid #cfe0ff; border-radius: 8px; padding: 11px; }
            QListWidget { background: #fbfcfe; border: 1px dashed #9eb2cf; border-radius: 9px; padding: 7px; outline: none; }
            QListWidget::item { padding: 9px; border-radius: 6px; }
            QListWidget::item:selected { background: #dbeafe; color: #173b70; }
            QComboBox, QDoubleSpinBox { background: white; border: 1px solid #b9c4d4; border-radius: 7px; padding: 7px 9px; min-height: 22px; }
            QComboBox:focus, QDoubleSpinBox:focus { border: 1px solid #2563eb; }
            QPushButton { background: white; border: 1px solid #b9c4d4; border-radius: 7px; padding: 8px 12px; }
            QPushButton:hover { background: #edf3fb; border-color: #8da4c4; }
            QPushButton:pressed { background: #e1e9f5; }
            QPushButton:disabled { color: #98a2b3; background: #eef1f5; }
            QPushButton#primary, QPushButton#primarySmall { background: #2563eb; border-color: #2563eb; color: white; font-weight: 650; }
            QPushButton#primary { padding: 11px 20px; }
            QPushButton#primary:hover, QPushButton#primarySmall:hover { background: #1d4ed8; }
            QFrame#bottomBar { background: white; border: 1px solid #dfe5ef; border-radius: 12px; }
            QLabel#status { font-weight: 600; }
            QProgressBar { border: none; background: #e6ebf2; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #2563eb; border-radius: 4px; }
        """)

    def _choose_files(self) -> None:
        filters = "Video (*.mp4 *.mov *.m4v *.avi *.mkv *.webm *.wmv *.mpg *.mpeg *.mts *.m2ts *.3gp *.ts)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Scegli le clip video", "", filters)
        self._add_files(paths)

    def _add_files(self, paths: list[str]) -> None:
        existing = {str(clip.path).lower() for clip in self.clips}
        errors: list[str] = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for raw in paths:
                path = Path(raw).resolve()
                if str(path).lower() in existing:
                    continue
                try:
                    info = inspect_clip(path, ffmpeg_path())
                    self.clips.append(info)
                    existing.add(str(path).lower())
                    self._append_item(info)
                except Exception as exc:
                    errors.append(f"{path.name}: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
        if self.clips and self.output_path is None:
            self.output_path = suggested_output_path(self.clips[0].path)
        self._update_summary()
        if errors:
            QMessageBox.warning(self, "Clip non aggiunte", "\n".join(errors))

    def _append_item(self, clip: ClipInfo) -> None:
        audio = "audio" if clip.has_audio else "senza audio"
        text = f"{self.clip_list.count() + 1}.  {clip.path.name}   ·   {format_duration(clip.duration)}   ·   {clip.width}×{clip.height}   ·   {audio}"
        item = QListWidgetItem(text)
        item.setToolTip(str(clip.path))
        self.clip_list.addItem(item)

    def _refresh_items(self) -> None:
        self.clip_list.clear()
        for clip in self.clips:
            self._append_item(clip)
        self._update_summary()

    def _move_selected(self, offset: int) -> None:
        row = self.clip_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= len(self.clips):
            return
        self.clips[row], self.clips[target] = self.clips[target], self.clips[row]
        self._refresh_items()
        self.clip_list.setCurrentRow(target)

    def _remove_selected(self) -> None:
        rows = sorted({self.clip_list.row(item) for item in self.clip_list.selectedItems()}, reverse=True)
        for row in rows:
            self.clips.pop(row)
        self._refresh_items()

    def _clear(self) -> None:
        self.clips.clear()
        self.clip_list.clear()
        self.output_path = None
        self._update_summary()

    def _choose_output(self) -> None:
        initial = str(self.output_path or (Path.home() / "video_unito.mp4"))
        path, _ = QFileDialog.getSaveFileName(self, "Salva il video unito", initial, "Video MP4 (*.mp4)")
        if path:
            self.output_path = Path(path).with_suffix(".mp4")
            self._update_summary()

    def _update_summary(self) -> None:
        duration = sum(clip.duration for clip in self.clips)
        size = sum(clip.path.stat().st_size for clip in self.clips if clip.path.exists())
        if self.clips:
            self.summary.setText(f"{len(self.clips)} clip · durata totale {format_duration(duration)} · {format_bytes(size)} originali")
        else:
            self.summary.setText("Nessuna clip selezionata")
        self.output_label.setText(str(self.output_path) if self.output_path else "La destinazione verrà proposta dopo aver aggiunto le clip")
        self.start_button.setEnabled(len(self.clips) >= 2 and self.thread is None)

    def _start(self) -> None:
        if len(self.clips) < 2 or not self.output_path:
            return
        if self.output_path.exists():
            answer = QMessageBox.question(
                self, "Sostituire il file?", f"Il file esiste già:\n\n{self.output_path}\n\nVuoi sostituirlo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.result = None
        self.open_folder_button.hide()
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self._set_controls(False)
        self.status_label.setText("Preparazione…")
        settings = MergeSettings(
            resolution=self.resolution_combo.currentData(),
            quality=self.quality_combo.currentData(),
            fps=30,
            black_gap_seconds=self.black_gap_spin.value() if self.black_gap_check.isChecked() else 0.0,
        )
        self.thread = QThread(self)
        self.worker = MergeWorker(list(self.clips), self.output_path, settings)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._on_finished)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _set_controls(self, enabled: bool) -> None:
        for widget in (
            self.add_button, self.up_button, self.down_button, self.remove_button,
            self.clear_button, self.clip_list, self.quality_combo, self.resolution_combo,
            self.black_gap_check, self.black_gap_spin, self.output_button, self.start_button,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self.black_gap_spin.setEnabled(self.black_gap_check.isChecked())

    def _on_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(value)
        self.status_label.setText(message)

    def _on_completed(self, result: MergeResult) -> None:
        self.result = result

    def _on_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Unione non riuscita", message)

    def _on_finished(self, cancelled: bool) -> None:
        self.thread = None
        self.worker = None
        self.cancel_button.hide()
        self._set_controls(True)
        self._update_summary()
        if cancelled:
            self.status_label.setText("Operazione annullata")
        elif self.result:
            self.progress_bar.setValue(100)
            self.status_label.setText(
                f"Completato: {self.result.clips} clip · {format_duration(self.result.duration)} · {format_bytes(self.result.output_bytes)}"
            )
            self.open_folder_button.show()
        else:
            self.status_label.setText("Operazione non completata")

    def _cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Annullamento in corso…")

    def _open_folder(self) -> None:
        if self.result:
            QDesktopServices.openUrl(self.result.output_path.parent.as_uri())

    def closeEvent(self, event) -> None:
        if self.thread and self.thread.isRunning():
            answer = QMessageBox.question(
                self, "Unione in corso", "Annullare l’unione e chiudere?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if self.worker:
                self.worker.cancel()
            self.setEnabled(False)
            self.thread.finished.connect(self.close)
            event.ignore()
            return
        event.accept()


def make_fallback_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#2563eb"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 13, 13)
    painter.setBrush(QColor("white"))
    painter.setPen(QPen(QColor("white"), 2))
    painter.drawPolygon(QPolygon([QPoint(25, 18), QPoint(25, 46), QPoint(47, 32)]))
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalTools.UnisciVideo")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("LocalTools")
    app.setStyle("Fusion")
    icon = QIcon(str(resource_path("assets/dog_video.ico")))
    app.setWindowIcon(icon if not icon.isNull() else make_fallback_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
