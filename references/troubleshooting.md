# Troubleshooting & Safety Notes

## 1. CS2 Updates vs. HLAE Compatibility
CS2 updates occasionally break `AfxHookSource2.dll` injection (`*.mdmp` crash dump next to `cs2.exe` or `"Error - AfxHookSource"` dialog).
- Run `uv run scripts/cs2_clip_maker.py doctor --output doctor.json` to compare CS2 `steam.inf` (`VersionDate`) against installed HLAE `published_at`.
- Once advancedfx ships an updated release, refresh tools automatically:
  ```powershell
  uv run scripts/cs2_clip_maker.py update-tools --output tools.json
  ```

## 2. VAC Safety & `gameinfo.gi` Cleanup
- HLAE launches CS2 **strictly** with `-insecure` and custom sandboxed user config (`USRLOCALCSGO=~/.cs2-clip-maker/cs2-cfg`).
- Recording temporarily copies `vendor/cs2-plugin/server.dll` into `<CS2>/game/csgo/csdm/bin/` and patches `gameinfo.gi`.
- `plugin.uninstall_plugin()` runs in a `try...finally` block to restore `gameinfo.gi` from `.backup` (or regex/line fallback) and delete the temporary `csdm/` folder.
- **Never join VAC-protected servers while an injected HLAE session is open.**

## 3. Microsoft Store Python & Work Directories
Windows Microsoft Store Python virtualizes writes inside `%TEMP%` and `%APPDATA%` under its MSIX container, making temporary files invisible to external non-packaged executables (`cs2.exe`, `HLAE.exe`, `ffmpeg.exe`).
- Default work/tool storage lives in `Path.home() / ".cs2-clip-maker"` (`~/.cs2-clip-maker`) and additionally checks existing `~/.cs2-highlights/tools` caches.
- Avoid passing `--work-dir` paths inside `%TEMP%`.

## 4. Demos Missing In-Game Voice Chat
HLTV/GOTV broadcasts and some third-party match configs strip team voice packets (`dp.parse_voice()` returns empty).
- `extract-voice` uses a soft fallback: creates an exact-length silent mono 48 kHz WAV and marks `voice_status: "empty"` so rendering continues normally with game sound and optional music.

## 5. Pre-existing `cs2.exe` Session
- By default, `record` and `run` abort cleanly if `cs2.exe` is already running so active gameplay is not disrupted.
- Close CS2 first, or pass `--kill-cs2` to terminate lingering background CS2 processes automatically.

## 6. Attributions
- `vendor/cs2-plugin/server.dll`: CS2 server plugin from [`akiver/cs-demo-manager`](https://github.com/akiver/cs-demo-manager) (MIT License, see [`vendor/cs2-plugin/NOTICE`](../vendor/cs2-plugin/NOTICE)).
- Recording automation & scoring base adapted from [`Rovniy/cs2-highlights-maker`](https://github.com/Rovniy/cs2-highlights-maker) (MIT License).
