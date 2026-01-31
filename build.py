import os
import subprocess
import sys
from pathlib import Path


def build() -> None:
    project_root = Path(__file__).resolve().parent
    bin_dir = project_root / "bin"

    add_binaries = []
    if sys.platform.startswith("win"):
        ffmpeg_name = "ffmpeg.exe"
        ffprobe_name = "ffprobe.exe"
        separator = ";"
    else:
        ffmpeg_name = "ffmpeg"
        ffprobe_name = "ffprobe"
        separator = ":"

    ffmpeg_path = bin_dir / ffmpeg_name
    ffprobe_path = bin_dir / ffprobe_name
    if ffmpeg_path.exists():
        add_binaries.append(f"{ffmpeg_path}{separator}bin")
    if ffprobe_path.exists():
        add_binaries.append(f"{ffprobe_path}{separator}bin")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "MP4HeadTrimmer",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "main.py",
    ]

    for binary in add_binaries:
        command.extend(["--add-binary", binary])

    env = os.environ.copy()
    subprocess.check_call(command, cwd=project_root, env=env)


if __name__ == "__main__":
    build()
