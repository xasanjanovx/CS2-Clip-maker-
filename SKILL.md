---
name: cs2-clip-maker
description: >-
  Generates Counter-Strike 2 highlight videos (9:16 vertical Shorts/Reels/TikTok
  with enlarged top killfeed and bottom info HUD cards, 4:5, or 16:9 widescreen)
  from any .dem, .dem.gz, .dem.zst, or .zip demo archive. Automatically excludes
  warmup/knife rounds, detects aces, 4K/3K multi-kills, 1vX clutches, 1-taps,
  Deagle/pistol highlights, or knife/Zeus kills, tight-trims idle gaps, records
  1080p 60fps gameplay via CS2 + HLAE, decodes Opus team voice chat, mixes
  optional music, and outputs an MP4. Use whenever asked to create CS2 clips,
  highlights, edits, clutches, aces, or vertical reels from CS2 demo files.
---

# CS2 Clip Maker

## Overview

End-to-end automated pipeline for turning raw CS2 match demos into ready-to-post highlight MP4s:
1. Unpack `.dem`, `.dem.gz`, `.dem.zst`, or `.zip` archives and filter warmup / opening knife rounds.
2. Curate moments by preset (`all`, `aces`, `clutches`, `deagle`, `pistols`, `1taps`, `knife_zeus`), tight-trim dead time, and generate localized HUD card labels.
3. Record 1920x1080 60fps POV footage inside CS2 via HLAE + `vendor/cs2-plugin/server.dll` (`-insecure`).
4. Decode in-game Opus team voice chat from the `.dem` stream via PyAV (soft-falling back to silence if voice data is absent).
5. Composite 9:16 vertical (blurred background, enlarged top killfeed crop, bottom HUD info cards) or 4:5 / 16:9 video with optional music via FFmpeg.

## Dependencies

- **`uv`**: Required Python environment and command runner (`uv run scripts/cs2_clip_maker.py ...`; if invoked from outside the skill directory, pass `uv run --project <skill-dir> <skill-dir>/scripts/cs2_clip_maker.py ...`). Never invoke bare `python` or `python3`. Ensure `uv` is installed and available before running commands (refer to the installed `uv` skill if missing).
- **Windows Recording Tools**: Steam running + CS2 installed + FFmpeg (`ffprobe`) + HLAE (validated via `doctor` and auto-installed via `update-tools` if missing). Cross-platform hosts can still run `analyze`, `extract-voice`, and `render` on existing footage.

## Quick Start

Always check the environment first, then run either the full pipeline (`run`) or step-by-step commands (`analyze` -> `record` -> `extract-voice` -> `render`).

```powershell
# 1. Verify CS2, Steam, HLAE, FFmpeg, and server.dll
uv run scripts/cs2_clip_maker.py doctor --output outputs/doctor.json

# 2. Full automated 9:16 vertical edit with enlarged top killfeed, bottom info cards, voice chat & music
uv run scripts/cs2_clip_maker.py run `
  --demos path\to\match1.dem path\to\match2.zip `
  --preset all `
  --limit 20 `
  --aspect 9:16 `
  --tight-trim `
  --music path\to\track.mp3 `
  --output outputs/highlights_vertical.mp4
```

## Utility Scripts

All commands reside in [scripts/cs2_clip_maker.py](./scripts/cs2_clip_maker.py), write full structured data to `--output` JSON files (keeping stdout concise), and return exit code `1` on failure. **Always pass `--limit` explicitly** (`--limit 0` means no cap / keep all matching highlights).

### `doctor` & `update-tools`
```powershell
uv run scripts/cs2_clip_maker.py doctor --output outputs/doctor.json
uv run scripts/cs2_clip_maker.py update-tools --output outputs/tools.json
```

### `analyze` — Parse, Filter, Tight-Trim & Generate HUD Manifest
```powershell
uv run scripts/cs2_clip_maker.py analyze `
  --demos path\to\demos_or_zip `
  --preset all `
  --limit 0 `
  --tight-trim `
  --max-clip-sec 34 `
  --lang ru `
  --exclude-round "Dust 2:10" `
  --output outputs/manifest.json
```
Read only needed fields from `outputs/manifest.json` (`selected_count`, `clips[].badge`, `clips[].duration_seconds`, `clips[].round`) before proceeding to long CS2 recordings so the user can review or drop specific clips.

### `record` — Capture 1080p 60fps Game Footage (CS2 + HLAE)
```powershell
uv run scripts/cs2_clip_maker.py record `
  --manifest outputs/manifest.json `
  --kill-cs2 `
  --output outputs/recorded.json
```
Groups clips per demo into one CS2 session and restores `gameinfo.gi` in a strict `finally` block.

### `extract-voice` — Decode In-Game Team Voice Chat
```powershell
uv run scripts/cs2_clip_maker.py extract-voice `
  --manifest outputs/recorded.json `
  --output outputs/voiced.json
```
Parses `.dem` Opus packets per speaker cleanly. If a demo has no voice stream (e.g., GOTV/HLTV), emits silent WAV stems without failing.

### `render` — Composite Final Vertical/Landscape Video
```powershell
uv run scripts/cs2_clip_maker.py render `
  --manifest outputs/voiced.json `
  --aspect 9:16 `
  --killfeed `
  --info-cards `
  --music path\to\music.mp3 `
  --output outputs/final_supercut.mp4
```

## Reference Guides

- Presets (`all`, `aces`, `clutches`, `deagle`, `pistols`, `1taps`, `knife_zeus`), tight-trimming rules, killfeed crop coordinates, and audio mix parameters: [references/presets_and_layouts.md](./references/presets_and_layouts.md).
- HLAE vs. CS2 crash diagnostics, VAC / `-insecure` safety, Microsoft Store Python caveats: [references/troubleshooting.md](./references/troubleshooting.md).

## Common Mistakes

- **Omitting `--limit` or leaving silent caps**: `--limit` is intentionally required on `analyze` and `run`. Pass `--limit 0` if you truly want every matching highlight across all demos.
- **Starting `record` with Steam closed or an active VAC session**: Ensure Steam is running and close any live CS2 window (or pass `--kill-cs2` only for offline cleanup).
- **Skipping `--tight-trim` on multi-kill clutches**: Without tight-trimming, rounds with an early entry kill + 40s bomb stall produce sluggish clips. Keep `--tight-trim` enabled unless the user specifically asks for full unedited rounds.
- **Expecting music files inside the skill repo**: Background tracks (`.mp3`/`.wav`) and `.dem`/`.mp4` binaries are excluded from git; pass local `--music <path>` explicitly when requested.
- **Using bare `python` or `%TEMP%` work directories**: On Windows Store Python, `%TEMP%`/`%APPDATA%` writes are MSIX-sandboxed and invisible to CS2/HLAE/FFmpeg. Always invoke via `uv run` and keep default work paths under `~/.cs2-clip-maker`.
