# Presets, Tight-Trimming, and Video Layouts

Detailed rules used by [`presets.py`](../src/cs2_clip_maker/presets.py), [`composer.py`](../src/cs2_clip_maker/composer.py), [`killfeed_cutout.py`](../src/cs2_clip_maker/killfeed_cutout.py), and [`template_montage.py`](../src/cs2_clip_maker/template_montage.py).

## Signature Vertical Reels Layout (`9:16` Default)

All vertical `9:16` (`1080x1920` @ `60.00 fps` CFR) outputs use the user's signature configuration modeled after `https://www.youtube.com/shorts/z0T2CTj0SfU`, with blurred top/bottom background instead of black bars and cut-out red-bordered killfeed pills:

| Region | Coordinates in `1080x1920` | FFmpeg / Processing Rule |
| :--- | :--- | :--- |
| **Top Blurred Bar** | `y = 0 .. 378` (`378 px` tall) | `crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10` (NEVER black bars) |
| **Center Gameplay Window (`4:3` Vertical Stretch)** | `x = 0 .. 1080`, `y = 378 .. 1502` (`1080x1124`) | `crop=1400:986:200:0,scale=1080:1124:flags=lanczos` overlaid at `0:378` (crops out bottom demo bar `y >= 986` and applies vertical stretch matching `z0T2CTj0SfU`) |
| **Bottom Blurred Bar** | `y = 1502 .. 1920` (`418 px` tall) | Filled by the continuous `1080x1920` blurred background (`boxblur=30:8`) |
| **Cut-Out Killfeed Pills (`--killfeed`)** | Stacked in top blurred bar above `y = 378` (`y_bot = 360`, `x_right = 1048`) | Extracted via `detect_killfeed_pills_pro()` along the player's red pill border `(rx0, ry0, rx1, ry1)`, scaled `1.48x` with Lanczos, with zero surrounding wall zoom |
| **Bottom HUD Info Card (`--info-cards`)** | `y = 1545` (inside bottom blurred bar) | Optional translucent badge card (`--no-info-cards` disables it for clean music montages) |

## Cut-Out Red-Bordered Killfeed Pill Detector (`killfeed_cutout.py`)

Instead of cropping and zooming a large `620x230` corner rectangle of the map wall, `detect_killfeed_pills_pro()` scans the Panorama killfeed region (`x = 1350..1918, y = 64..260`) for the local player's 2px horizontal red border ridges (`R - G` contrast between border rows separated by `dy = 28..32 px` and the adjacent interior/exterior rows):
- Works reliably across bright sky, dark shadows, terracotta roofs, orange walls, and red damage vignettes.
- Rejects teammate/enemy kills that do not have the player's red border.
- Cuts out each individual pill `(rx0, ry0, rx1, ry1)` cleanly and stacks them at `y_bot = 360, x_right = 1048`.

## 1-to-1 Template Montage Rules (`template_montage.py`)

1. **Zero Duplicate Moments**: Every beat/kill slot (`pov_kill`) is mapped to a distinct `(demo, round, kill_tick)`.
2. **Best Highlights Priority**: Ranks rounds by multi-kill count (`5K > 4K > 3K`), pistol 1-tap headshots (`USP-S` / `Deagle` 3K 1-taps), AWP multi-kills/noscopes, and won clutches.
3. **1-to-1 Matched 3rd-Person Victim Deaths**:
   - Attacker POV is recorded with `spec_mode 1; spec_player <attacker_slot>` (`kill_tick - 128 .. kill_tick + 112`, so shot lands at `t ≈ 2.017s`).
   - Victim 3rd-person death is recorded with `spec_mode 2; spec_player <victim_slot>` (`kill_tick - 24 .. kill_tick + 104`, so death occurs at `t ≈ 0.375s`).
   - Every 3D death cut (`<id>_3d.mp4`) is placed **immediately after its own `<id>_pov.mp4`**, ensuring the enemy falls in the exact same map location where the player just shot them.
