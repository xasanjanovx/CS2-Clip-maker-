---
name: cs2-clip-maker
description: >-
  Generates Counter-Strike 2 highlight videos and 1-to-1 beat-synced template
  fragmovies in signature 9:16 vertical Reels/Shorts format (1080x1920 @ 60fps CFR
  with 4:3 gameplay stretched vertically to 1080x1124 at y=378..1502 like
  z0T2CTj0SfU, blurred top y=0..378 & bottom y=1502..1920 background instead of
  black bars, and cleanly cut-out red-bordered killfeed pills at y_bot=360) from
  any .dem, .dem.gz, .dem.zst, or .zip demo archive. Supports 1-to-1 template
  montage replication (beat-synced POV kills + 1-to-1 matched 3rd-person victim
  deaths from the exact same kill tick), multi-kill curation (aces, 4K/3K,
  USP-S/Deagle 1-tap headshots, AWP noscopes, clutches), HLAE + server.dll
  recording, and Opus voice chat extraction.
---

# CS2 Clip Maker & 1-to-1 Template Montage Skill

## Signature Vertical Reels Format & Mandatory Rules

Whenever the user provides a **template video / YouTube / Shorts link** and **CS2 demo archives (`.dem` / `.zip`)** (or asks for a vertical CS2 montage/Reels edit), ALWAYS apply these exact settings by default without needing to re-ask:

1. **Vertical 9:16 Reels Layout (`1080x1920` @ `60.00 fps` CFR)**:
   - **Center Gameplay Window (`4:3` stretched vertically to `1080x1124` at `y = 378..1502`)**:
     Crop the `4:3` gameplay region `crop=1400:986:200:0` (removing the bottom demo playback bar `y >= 986` and staying left of the top-right killfeed) and scale to **`1080x1124`** (`scale=1080:1124:flags=lanczos`), overlaid at `x = 0, y = 378` (matching the exact window geometry and vertical stretch of `https://www.youtube.com/shorts/z0T2CTj0SfU`).
   - **Top (`y = 0..378`) & Bottom (`y = 1502..1920`) Blurred Background (NEVER Black Bars)**:
     Fill the top `378px` and bottom `418px` with the blurred gameplay background (`crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10`). **Never leave black bars at the top and bottom.**
   - **Cut-Out Red-Bordered Killfeed Pills at Top (`y_bot = 360, x_right = 1048`)**:
     Use [`killfeed_cutout.py`](./src/cs2_clip_maker/killfeed_cutout.py) (`detect_killfeed_pills_pro` + `build_pill_overlay_chain`) to cut out **only** the player's red-bordered killfeed pills along their exact `(rx0, ry0, rx1, ry1)` border (`1.48x` Lanczos scale) and paste them in the top blurred bar right above the gameplay window (`y_bot = 360`, right above `y = 378`). **Never zoom a giant rectangle of surrounding wall and never include non-player kills.**

2. **1-to-1 Template Montage Rules (`template-montage`)**:
   - **Zero Repeated Moments**: Every kill slot in the montage must use a **100% unique** kill `(demo, kill_tick)`. Never duplicate the same clip across multiple beat slots.
   - **Best Moments Only (No Easy 1K Filler)**: Prioritize 5K Aces, 4Ks, **3K USP-S / Deagle 1-Tap Headshot rounds**, AWP 4K/3K/noscopes, and won 1vX clutches.
   - **Zero Footage from the Template Video — 1-to-1 Matched 3rd-Person Enemy Deaths**:
     Never copy video frames from the reference template video. Record 3rd-person victim deaths (`spec_mode 2` on the exact `<victim_slot>` across `kill_tick - 24 .. kill_tick + 104`) from the user's own demos, and place each `<kill>_3d.mp4` **immediately after its exact matching `<kill>_pov.mp4`** (same map, same round, same weapon, same victim, exact same `kill_tick`). Discard any obstructed 3D angle where a wall or other player blocks the camera.
   - **Exact Beat Sync & Impact VFX (No Fake Headshot SFX)**:
     Lock every shot frame `src_hit` to the exact beat/shot timestamp `hit_t` (`n_pre = round(hit_t*60) - round(t_start*60)`, `n_post = round(t_end*60) - round(hit_t*60)`, `0` cumulative drift), with white exposure flash, RGB chromatic aberration (`rgbashift`), and recoil micro-shake on the exact kill frame. Do **not** add separate artificial headshot sound effects.

---

## Quick Start

### 1. 1-to-1 Beat-Synced Template Montage (`template-montage`)
When the user sends a reference YouTube/Shorts link (or `.mp4`) + match demos (`.zip` / `.dem`):

```powershell
uv run scripts/cs2_clip_maker.py template-montage `
  --template "https://www.youtube.com/watch?v=cPdbef-JMws" `
  --demos match_13613505.zip match_13605454.zip match_13352820.zip match_13405623.zip match_13650629.zip `
  --player "DontBlink" `
  --output outputs/DontBlink_Vertical_Reels.mp4
```

### 2. Standard Curated Highlights Pipeline (`run`)
```powershell
# Verify CS2, Steam, HLAE, FFmpeg, and server.dll
uv run scripts/cs2_clip_maker.py doctor --output outputs/doctor.json

# Full automated 9:16 vertical edit (1080x1124 stretched 4:3 window + blurred top/bottom + cut-out killfeed pills)
uv run scripts/cs2_clip_maker.py run `
  --demos path\to\match1.dem path\to\match2.zip `
  --player "DontBlink" `
  --preset all `
  --limit 20 `
  --aspect 9:16 `
  --tight-trim `
  --music path\to\track.mp3 `
  --output outputs/highlights_vertical.mp4
```

---

## Step-by-Step CLI Subcommands

All commands reside in [scripts/cs2_clip_maker.py](./scripts/cs2_clip_maker.py):

- **`template-montage`**: End-to-end 1-to-1 beat-synced montage from `--template <url_or_mp4>` and `--demos <paths>` ([`src/cs2_clip_maker/template_montage.py`](./src/cs2_clip_maker/template_montage.py)).
- **`doctor` & `update-tools`**: Validate and auto-install HLAE, FFmpeg, and `vendor/cs2-plugin/server.dll`.
- **`analyze`**: Parse `.dem`/`.zip` archives, filter warmup/knife rounds, rank highlights, and write manifest JSON.
- **`record`**: Capture 1920x1080 60fps footage inside CS2 via HLAE + `server.dll` (`demo_ui_mode 0`, `cl_draw_only_deathnotices 1`, `cl_trueview_show_status 0`).
- **`extract-voice`**: Decode in-game Opus team voice chat from `.dem` files via PyAV.
- **`render`**: Composite 9:16 (`1080x1124` window at `y=378` + blurred top/bottom + cut-out red-bordered killfeed pills at `y_bot=360`), `4:5`, or `16:9` MP4 ([`src/cs2_clip_maker/composer.py`](./src/cs2_clip_maker/composer.py)).

## Reference Guides

- Presets, tight-trimming rules, `z0T2CTj0SfU` vertical 4:3 stretch coordinates, and cut-out killfeed pill detector: [references/presets_and_layouts.md](./references/presets_and_layouts.md).
- HLAE vs. CS2 crash diagnostics, `server.dll` tick-exact recording, and Windows paths: [references/troubleshooting.md](./references/troubleshooting.md).
