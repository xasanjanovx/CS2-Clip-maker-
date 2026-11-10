# Presets, Tight-Trimming, and Video Layouts

Detailed rules used by [`presets.py`](../src/cs2_clip_maker/presets.py) and [`composer.py`](../src/cs2_clip_maker/composer.py).

## Highlight Presets (`--preset`)

Before preset filtering runs, `parse_demo()` and `presets.py` automatically strip:
- Warmup / DM periods (`is_warmup_period`, ticks before `begin_new_match`, and any synthetic warmup round with `> 10` deaths).
- Opening knife rounds (`>= 4` knife or Zeus kills in a round), unless `--preset knife_zeus --include-knife-rounds` is explicitly set.

| Preset | Selection Rule | Notes |
| :--- | :--- | :--- |
| `all` | Aces, 4Ks, won 1vX clutches, 3Ks, Deagle 2K+, rapid 2K HS (`<= 3.5s`), 2+ one-taps, knife, Zeus, AWP noscope | Best balanced highlights reel |
| `aces` | `5+` kills shown in a single clip | Full 5K aces (`<= 60s` span) are preserved by tight-trim; pass `--no-tight-trim` for slow 90s+ rounds |
| `clutches` | Won `1vX` rounds (`X >= 1`) | Trims trailing post-kill bomb waiting while keeping finish |
| `deagle` | `>= 2` Desert Eagle kills in round (`>= 1` if `--min-kills 1`) | Great for sharp Deagle reels |
| `pistols` | `>= 2` pistol kills or MR12 pistol rounds (R1 / R13) | Includes USP-S, Glock, P250, Five-SeveN, Tec-9, CZ75, Deagle |
| `1taps` | `>= 2` headshots in rapid burst (`<= 4.5s`) or verified 1-hit kills from `player_hurt` | Cross-references `player_hurt` (`<= 1` hit in 3s window before lethal headshot) |
| `knife_zeus` | `>= 1` Knife, Bayonet, or Zeus x27 kill | Add `--include-knife-rounds` to include match-start knife rounds |

## Tight-Trimming Algorithm (`--tight-trim` / `--no-tight-trim`)

Long CS2 rounds often have 35-55 seconds of rotation or saving between an early entry frag and a final 3K. When `--tight-trim` is active (default):

1. Kills within a `(player, round)` moment are split into dense clusters whenever consecutive kills exceed `--tight-gap-sec` (default `18.0s`).
2. Each cluster is bounded within `--max-clip-sec` (default `34.0s`) via a sliding window maximizing kills, headshots, one-taps, specials, and shortest action span.
3. For won clutches where the last enemy dies early and a 30s bomb wait follows, the tail stops shortly after the final clutch kill/defuse (`~4.5s`) rather than idling until `round_end`.
4. `badge` and `subtitle` reflect `kills_shown` and `headshots_shown` (honest labels: if an opening kill 45s earlier was trimmed out of a 4K, the card says `3K`).

## Aspect Ratios and Overlays (`--aspect`)

Source captures from HLAE are `1920x1080` at `60fps` (non-1080p inputs are normalized automatically).

| Layout | Output Size | Main Viewport | Enlarged Top Killfeed (`--killfeed`) | Bottom HUD Info Card (`--info-cards`) |
| :--- | :--- | :--- | :--- | :--- |
| `9:16` | `1080x1920` | `1080x608` centered at `y=656` over blurred dimmed background | Crops top-right `560x130+1340+50` -> scales `962x223`, centered at `y=415` (`white@0.35` frame), activates at `first_kill_offset - 0.35s` | Translucent card at `y=1350`, gold outline for Aces/Clutches/Knife, cyan for multi-kills |
| `9:16-clean` | `1080x1920` | `1080x608` centered at `y=656` | Disabled | Same as `9:16` (disable with `--no-info-cards`) |
| `4:5` | `1080x1350` | `1080x608` centered at `y=371` | Crops top-right `560x130+1340+50` -> scales `840x195` at `y=130` | Centered at `y=1030` |
| `16:9` | `1920x1080` | Native `1920x1080` fullscreen | Uses native in-game top-right killfeed (extra overlay not needed) | Centered banner near bottom (`y=925`) |

## Audio Mixing Graph

Per clip in [`composer.py`](../src/cs2_clip_maker/composer.py):
- **Game audio (`0:a`):** `volume=1.05`, stereo 48 kHz.
- **Voice stem (`voice_wav`):** `demoparser2.parse_voice()` Opus packets decoded per speaker via PyAV `48000 Hz`, `highpass=f=100`, `volume=2.5`, `alimiter=limit=0.95`. Falls back to silent WAV if demo contains no voice data.
- **Music (`--music`):** Continuous offset tracked across sequential clips modulo track length (`atrim`), `volume=0.24`, `afade` `0.3s` in / `0.4s` out.
- **Master bus:** `amix=inputs=2|3:duration=first:normalize=0`, `alimiter=limit=0.96`.
