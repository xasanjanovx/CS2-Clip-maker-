"""Extract and decode CS2 in-game Opus team voice chat from .dem files into WAV stems."""

from __future__ import annotations

import struct
import wave
from collections import defaultdict
from pathlib import Path

import av
import numpy as np
from demoparser2 import DemoParser

SAMPLE_RATE = 48000


def write_silence_wav(path: Path, duration_seconds: float, sample_rate: int = SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = max(sample_rate // 10, int(round(max(0.1, duration_seconds) * sample_rate)))
    silence = np.zeros(num_samples, dtype="<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence.tobytes())
    return path


def _decode_speaker_packets(
    packets: list[dict],
    start_tick: int,
    tickrate: float,
    total_samples: int,
    sample_rate: int = SAMPLE_RATE,
) -> np.ndarray:
    buf = np.zeros(total_samples + sample_rate * 2, dtype=np.float32)
    codec = av.CodecContext.create("opus", "r")
    codec.extradata = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 0, sample_rate, 0, 0)

    cursor = 0
    for ev in packets:
        raw_bytes = ev.get("bytes")
        if not raw_bytes:
            continue
        try:
            frames = codec.decode(av.Packet(bytes(raw_bytes)))
        except Exception:
            continue
        tick = int(ev.get("tick", start_tick))
        for frame in frames:
            arr = frame.to_ndarray().astype(np.float32).reshape(-1)
            if arr.size == 0:
                continue
            target_pos = int((tick - start_tick) / tickrate * sample_rate)
            pos = max(0, target_pos, cursor)
            if pos + len(arr) <= len(buf):
                buf[pos : pos + len(arr)] += arr
                cursor = pos + len(arr)
    return buf[:total_samples]


def extract_voices_for_manifest(manifest: dict, voice_dir: Path) -> dict:
    """Decode Opus voice WAVs for every clip in manifest; softly writes silence if voice is absent."""
    voice_dir.mkdir(parents=True, exist_ok=True)
    clips = manifest.get("clips", [])
    by_demo: dict[str, list[dict]] = defaultdict(list)
    for clip in clips:
        by_demo[clip["demo_path"]].append(clip)

    for demo_path_str, demo_clips in by_demo.items():
        voice_events: list[dict] = []
        voice_parse_error: str | None = None
        try:
            parser = DemoParser(demo_path_str)
            parsed = parser.parse_voice()
            if parsed:
                voice_events = list(parsed)
        except Exception as exc:
            voice_parse_error = str(exc)

        for clip in demo_clips:
            clip_id = clip["clip_id"]
            out_wav = voice_dir / f"{clip_id}_voice.wav"
            start_tick = int(clip["start_tick"])
            end_tick = int(clip["end_tick"])
            tickrate = float(clip.get("tickrate", 64.0) or 64.0)
            dur_sec = max(0.1, (end_tick - start_tick) / tickrate)
            total_samples = max(SAMPLE_RATE // 10, int(round(dur_sec * SAMPLE_RATE)))

            if voice_parse_error is not None or not voice_events:
                write_silence_wav(out_wav, dur_sec, SAMPLE_RATE)
                clip["voice_wav"] = str(out_wav.resolve())
                clip["voice_packets"] = 0
                clip["voice_status"] = "empty" if voice_parse_error is None else f"fallback_silence ({voice_parse_error})"
                continue

            sub_voice = [
                e for e in voice_events if start_tick <= int(e.get("tick", -1)) <= end_tick
            ]
            if not sub_voice:
                write_silence_wav(out_wav, dur_sec, SAMPLE_RATE)
                clip["voice_wav"] = str(out_wav.resolve())
                clip["voice_packets"] = 0
                clip["voice_status"] = "no_packets_in_window"
                continue

            speakers: dict[str, list[dict]] = defaultdict(list)
            for ev in sub_voice:
                speakers[str(ev.get("steamid", "unknown"))].append(ev)

            mix = np.zeros(total_samples, dtype=np.float32)
            for spk_packets in speakers.values():
                spk_packets.sort(key=lambda e: int(e.get("tick", 0)))
                mix += _decode_speaker_packets(spk_packets, start_tick, tickrate, total_samples)

            peak = float(np.abs(mix).max()) if mix.size > 0 else 0.0
            if peak > 0.01:
                mix = mix / peak * 0.96
                status = "ok"
            else:
                status = "silent"

            with wave.open(str(out_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes((np.clip(mix, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())

            clip["voice_wav"] = str(out_wav.resolve())
            clip["voice_packets"] = len(sub_voice)
            clip["voice_status"] = status

    return manifest
