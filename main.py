import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "MP4 Head Trimmer"


@dataclass
class FileJob:
    input_path: Path
    output_path: Path
    display_name: str


@dataclass
class FfmpegPaths:
    ffmpeg: Path
    ffprobe: Path


def locate_ffmpeg(app_dir: Path) -> Optional[FfmpegPaths]:
    local_ffmpeg = app_dir / "bin" / "ffmpeg"
    local_ffprobe = app_dir / "bin" / "ffprobe"
    if sys.platform.startswith("win"):
        local_ffmpeg = local_ffmpeg.with_suffix(".exe")
        local_ffprobe = local_ffprobe.with_suffix(".exe")

    if local_ffmpeg.exists() and local_ffprobe.exists():
        return FfmpegPaths(local_ffmpeg, local_ffprobe)

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    if ffmpeg_path and ffprobe_path:
        return FfmpegPaths(Path(ffmpeg_path), Path(ffprobe_path))

    return None


def is_mp4(path: Path) -> bool:
    return path.suffix.lower() == ".mp4"


def build_output_name(input_path: Path, trim_seconds: float, overwrite: bool) -> str:
    if overwrite:
        return input_path.name
    suffix = f"_trim{trim_seconds:g}s"
    return f"{input_path.stem}{suffix}{input_path.suffix}"


class Worker(QObject):
    progress = Signal(int, str, str)
    log = Signal(str)
    overall = Signal(int, int)
    finished = Signal()

    def __init__(
        self,
        jobs: List[FileJob],
        trim_seconds: float,
        overwrite: bool,
        ffmpeg_paths: FfmpegPaths,
    ) -> None:
        super().__init__()
        self.jobs = jobs
        self.trim_seconds = trim_seconds
        self.overwrite = overwrite
        self.ffmpeg_paths = ffmpeg_paths
        self._cancel = False
        self._current_process: Optional[subprocess.Popen[str]] = None

    def cancel(self) -> None:
        self._cancel = True
        if self._current_process and self._current_process.poll() is None:
            self._current_process.terminate()

    def _probe_duration(self, input_path: Path) -> Optional[float]:
        command = [
            str(self.ffmpeg_paths.ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            self.log.emit(f"Failed to run ffprobe: {exc}")
            return None

        duration_text = result.stdout.strip()
        if not duration_text:
            self.log.emit(f"ffprobe returned no duration for {input_path}.")
            return None
        try:
            return float(duration_text)
        except ValueError:
            self.log.emit(
                f"Could not parse duration '{duration_text}' for {input_path}."
            )
            return None

    def _run_ffmpeg(self, input_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
        command = [
            str(self.ffmpeg_paths.ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y" if self.overwrite else "-n",
            "-ss",
            f"{self.trim_seconds}",
            "-i",
            str(input_path),
            "-c",
            "copy",
            "-map",
            "0",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._current_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = self._current_process.communicate()
        return subprocess.CompletedProcess(command, self._current_process.returncode, stdout, stderr)

    @Slot()
    def run(self) -> None:
        total = len(self.jobs)
        for index, job in enumerate(self.jobs, start=1):
            if self._cancel:
                self.progress.emit(index - 1, "Canceled", "Canceled by user")
                break

            self.overall.emit(index, total)
            self.progress.emit(index - 1, "Processing", "")

            duration = self._probe_duration(job.input_path)
            if duration is not None:
                if duration <= self.trim_seconds + 0.01:
                    message = (
                        f"Duration {duration:.2f}s is shorter than trim {self.trim_seconds:.2f}s"
                    )
                    self.progress.emit(index - 1, "Skipped", message)
                    self.log.emit(f"Skipping {job.input_path}: {message}")
                    continue
            else:
                self.log.emit(
                    f"Warning: proceeding without duration info for {job.input_path}."
                )

            job.output_path.parent.mkdir(parents=True, exist_ok=True)
            if job.output_path.exists() and not self.overwrite:
                message = "Output exists and overwrite is disabled"
                self.progress.emit(index - 1, "Skipped", message)
                self.log.emit(f"Skipping {job.output_path}: {message}")
                continue

            result = self._run_ffmpeg(job.input_path, job.output_path)
            if result.returncode != 0:
                error_text = result.stderr.strip() or "Unknown ffmpeg error"
                self.progress.emit(index - 1, "Failed", error_text)
                self.log.emit(f"ffmpeg failed for {job.input_path}: {error_text}")
                continue

            if result.stderr.strip():
                self.log.emit(result.stderr.strip())

            self.progress.emit(index - 1, "Done", "")

        self.overall.emit(total, total)
        self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(900, 650)

        self.ffmpeg_paths = locate_ffmpeg(Path(__file__).resolve().parent)
        if not self.ffmpeg_paths:
            QMessageBox.critical(
                self,
                "FFmpeg Missing",
                "FFmpeg and ffprobe are required.\n"
                "Install FFmpeg or place binaries in ./bin (ffmpeg, ffprobe).",
            )

        self.file_paths: List[Path] = []
        self.worker_thread: Optional[QThread] = None
        self.worker: Optional[Worker] = None

        self._build_ui()

    def _build_ui(self) -> None:
        container = QWidget()
        main_layout = QVBoxLayout(container)

        source_group = QGroupBox("Source")
        source_layout = QGridLayout(source_group)
        self.source_edit = QLineEdit()
        self.source_button = QPushButton("Browse")
        self.source_button.clicked.connect(self._choose_source)
        source_layout.addWidget(QLabel("Source folder:"), 0, 0)
        source_layout.addWidget(self.source_edit, 0, 1)
        source_layout.addWidget(self.source_button, 0, 2)

        self.output_edit = QLineEdit()
        self.output_button = QPushButton("Browse")
        self.output_button.clicked.connect(self._choose_output)
        source_layout.addWidget(QLabel("Output folder:"), 1, 0)
        source_layout.addWidget(self.output_edit, 1, 1)
        source_layout.addWidget(self.output_button, 1, 2)

        settings_group = QGroupBox("Settings")
        settings_layout = QGridLayout(settings_group)

        self.trim_spin = QDoubleSpinBox()
        self.trim_spin.setDecimals(2)
        self.trim_spin.setMinimum(0.01)
        self.trim_spin.setSingleStep(0.1)
        self.trim_spin.setValue(2.0)
        self.trim_spin.setToolTip(
            "Stream-copy trimming may cut to the nearest keyframe and may not be frame-exact."
        )

        self.include_subfolders = QCheckBox("Include subfolders")
        self.include_subfolders.stateChanged.connect(self._toggle_preserve_structure)
        self.preserve_structure = QCheckBox("Preserve folder structure in output")
        self.preserve_structure.setEnabled(False)

        self.overwrite_check = QCheckBox("Overwrite existing output files")

        keyframe_note = QLabel(
            "Note: Stream-copy trimming may cut to the nearest keyframe and may not be frame-exact."
        )
        keyframe_note.setWordWrap(True)

        settings_layout.addWidget(QLabel("Trim seconds:"), 0, 0)
        settings_layout.addWidget(self.trim_spin, 0, 1)
        settings_layout.addWidget(self.include_subfolders, 1, 0, 1, 2)
        settings_layout.addWidget(self.preserve_structure, 2, 0, 1, 2)
        settings_layout.addWidget(self.overwrite_check, 3, 0, 1, 2)
        settings_layout.addWidget(keyframe_note, 4, 0, 1, 2)

        actions_layout = QHBoxLayout()
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._scan_files)
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self._start_processing)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_processing)
        self.cancel_button.setEnabled(False)
        actions_layout.addWidget(self.scan_button)
        actions_layout.addWidget(self.start_button)
        actions_layout.addWidget(self.cancel_button)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Message"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)

        self.progress_bar = QProgressBar()
        self.progress_label = QLabel("Processed 0 of 0")
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setPlaceholderText("Log output...")

        main_layout.addWidget(source_group)
        main_layout.addWidget(settings_group)
        main_layout.addLayout(actions_layout)
        main_layout.addWidget(self.table)
        main_layout.addLayout(progress_layout)
        main_layout.addWidget(QLabel("Log"))
        main_layout.addWidget(self.log_panel)

        self.setCentralWidget(container)

    def _toggle_preserve_structure(self) -> None:
        self.preserve_structure.setEnabled(self.include_subfolders.isChecked())
        if not self.include_subfolders.isChecked():
            self.preserve_structure.setChecked(False)

    def _choose_source(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if directory:
            self.source_edit.setText(directory)
            output_path = Path(directory) / "trimmed"
            self.output_edit.setText(str(output_path))

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if directory:
            self.output_edit.setText(directory)

    def _scan_files(self) -> None:
        source = Path(self.source_edit.text().strip())
        if not source.exists():
            QMessageBox.warning(self, "Missing Source", "Select a valid source folder.")
            return

        files: List[Path] = []
        if self.include_subfolders.isChecked():
            for path in source.rglob("*"):
                if path.is_file() and is_mp4(path):
                    files.append(path)
        else:
            for path in source.iterdir():
                if path.is_file() and is_mp4(path):
                    files.append(path)

        files.sort()
        self.file_paths = files
        self.table.setRowCount(0)

        for file_path in files:
            display_name = (
                str(file_path.relative_to(source))
                if self.include_subfolders.isChecked()
                else file_path.name
            )
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(display_name))
            self.table.setItem(row, 1, QTableWidgetItem("Pending"))
            self.table.setItem(row, 2, QTableWidgetItem(""))

        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(files) if files else 1)
        self.progress_label.setText(f"Processed 0 of {len(files)}")
        self.log_panel.appendPlainText(f"Found {len(files)} MP4 file(s).")

    def _start_processing(self) -> None:
        if not self.ffmpeg_paths:
            QMessageBox.critical(
                self,
                "FFmpeg Missing",
                "FFmpeg and ffprobe are required.\n"
                "Install FFmpeg or place binaries in ./bin (ffmpeg, ffprobe).",
            )
            return

        source_text = self.source_edit.text().strip()
        output_text = self.output_edit.text().strip()
        source = Path(source_text)
        output_root = Path(output_text)
        if not source.exists():
            QMessageBox.warning(self, "Missing Source", "Select a valid source folder.")
            return
        if not output_text:
            QMessageBox.warning(self, "Missing Output", "Select a valid output folder.")
            return

        trim_seconds = self.trim_spin.value()
        if trim_seconds <= 0:
            QMessageBox.warning(self, "Invalid Trim", "Trim seconds must be greater than 0.")
            return

        if not self.file_paths:
            self._scan_files()

        if not self.file_paths:
            QMessageBox.information(self, "No Files", "No MP4 files found to process.")
            return

        overwrite = self.overwrite_check.isChecked()
        include_subfolders = self.include_subfolders.isChecked()
        preserve_structure = self.preserve_structure.isChecked() and include_subfolders

        jobs = []
        for row in range(self.table.rowCount()):
            display_name = self.table.item(row, 0).text()
            input_path = source / display_name if include_subfolders else source / display_name
            relative_dir = Path(display_name).parent if preserve_structure else Path()
            output_dir = output_root / relative_dir
            output_name = build_output_name(input_path, trim_seconds, overwrite)
            output_path = output_dir / output_name
            jobs.append(FileJob(input_path=input_path, output_path=output_path, display_name=display_name))

        self.worker_thread = QThread()
        self.worker = Worker(jobs, trim_seconds, overwrite, self.ffmpeg_paths)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._update_progress)
        self.worker.log.connect(self._append_log)
        self.worker.overall.connect(self._update_overall)
        self.worker.finished.connect(self._processing_finished)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.scan_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        self.worker_thread.start()

    def _cancel_processing(self) -> None:
        if self.worker:
            self.worker.cancel()
            self._append_log("Cancel requested; stopping after current file.")

    @Slot(int, str, str)
    def _update_progress(self, row: int, status: str, message: str) -> None:
        if row < self.table.rowCount():
            self.table.setItem(row, 1, QTableWidgetItem(status))
            self.table.setItem(row, 2, QTableWidgetItem(message))

    @Slot(str)
    def _append_log(self, message: str) -> None:
        self.log_panel.appendPlainText(message)

    @Slot(int, int)
    def _update_overall(self, current: int, total: int) -> None:
        self.progress_bar.setMaximum(total if total else 1)
        self.progress_bar.setValue(min(current, total))
        self.progress_label.setText(f"Processed {min(current, total)} of {total}")

    def _processing_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._append_log("Processing complete.")


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
