"""Thin subprocess wrapper around ffmpeg/ffprobe with readable UTF-8 errors."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def find_ffprobe(ffmpeg_exe: Path) -> str:
    candidate = ffmpeg_exe.parent / ("ffprobe.exe" if ffmpeg_exe.suffix.lower() == ".exe" else "ffprobe")
    return str(candidate) if candidate.is_file() else "ffprobe"


def run_ffmpeg(ffmpeg_exe: Path, args: list[str]) -> None:
    cmd = [str(ffmpeg_exe), "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-12:]
        raise FfmpegError(
            "ffmpeg failed (exit {}):\n  {}\ncommand: {}".format(
                result.returncode, "\n  ".join(tail), subprocess.list2cmdline(cmd)
            )
        )


def probe_media(ffmpeg_exe: Path, media_path: Path) -> dict:
    probe_cmd = find_ffprobe(ffmpeg_exe)
    result = subprocess.run(
        [
            probe_cmd,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise FfmpegError(f"ffprobe failed for {media_path}: {result.stderr}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    fps = 0.0
    width = 0
    height = 0
    if video_streams:
        vs = video_streams[0]
        width = int(vs.get("width", 0) or 0)
        height = int(vs.get("height", 0) or 0)
        rate_str = str(vs.get("r_frame_rate") or vs.get("avg_frame_rate") or "0/1")
        if "/" in rate_str:
            num, _, den = rate_str.partition("/")
            try:
                fps = round(float(num) / max(float(den), 1.0), 3)
            except ValueError:
                fps = 0.0

    duration = 0.0
    try:
        duration = float(fmt.get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0

    size_bytes = 0
    try:
        size_bytes = int(fmt.get("size", 0) or media_path.stat().st_size)
    except (OSError, TypeError, ValueError):
        size_bytes = 0

    return {
        "path": str(media_path),
        "duration_seconds": round(duration, 3),
        "size_bytes": size_bytes,
        "width": width,
        "height": height,
        "fps": fps,
        "has_video": bool(video_streams),
        "has_audio": bool(audio_streams),
    }


def probe_duration(ffmpeg_exe: Path, media_path: Path) -> float:
    return float(probe_media(ffmpeg_exe, media_path)["duration_seconds"])
