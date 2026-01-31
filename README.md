# MP4 Head Trimmer

MP4 Head Trimmer is a cross-platform desktop app that batch-trims the first **X seconds** from MP4 files in a chosen folder **without re-encoding** (stream copy). It uses FFmpeg for fast, codec-preserving trimming.

## Features
- Select a source folder of `.mp4` files (optionally include subfolders).
- Choose an output folder (defaults to a `trimmed` subfolder in the source).
- Trim a floating-point number of seconds (e.g., `2.5`).
- Optional folder structure preservation when scanning subfolders.
- Per-file status updates, progress tracking, and detailed logs.

## Keyframe Caveat
Stream-copy trimming (`-c copy`) trims on keyframes, so the cut may not be frame-exact. This is expected with re-encoding disabled.

## Requirements
- Python 3.11+
- FFmpeg + ffprobe (either in `./bin` or available on your PATH)

## Install (Development)
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python main.py
```

## FFmpeg Setup
The app searches for `./bin/ffmpeg` and `./bin/ffprobe` first. If not found, it falls back to system PATH. If neither is available, it will show a dialog with instructions.

## Packaging (PyInstaller)
This project includes a simple build script that creates a single-folder build and bundles local FFmpeg binaries if present.

```bash
python build.py
```

The build output will be in `dist/MP4HeadTrimmer`.

### Notes for Windows/macOS/Linux
- On Windows, include `ffmpeg.exe` and `ffprobe.exe` in `./bin`.
- On macOS/Linux, include `ffmpeg` and `ffprobe` in `./bin`.

## License
MIT
