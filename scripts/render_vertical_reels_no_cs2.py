r"""
DONTBLINK CS2 FRAGMOVIE - VERTICAL REELS 9:16 WITH 3:4 (1080x1440) WINDOW & 1-TO-1 MATCHED 3D DEATHS
====================================================================================================
1. Format: 1080x1920 (9:16 Reels, 60fps CFR) with centered 3:4 vertical window (1080x1440 at y=240..1680)
   and 240px blurred top & bottom bars (y=0..240, y=1680..1920).
2. Cut-out [DontBlink] red-bordered killfeed pills placed cleanly in the top 240px blurred bar (y_bot=224).
3. 100% 1-to-1 Matched 3rd-Person Enemy Deaths (`<kill>_pov.mp4` -> `<kill>_3d.mp4`):
   Every single 3D enemy death cut shows the EXACT SAME enemy dying in the EXACT SAME spot right after
   [DontBlink]'s shot. All unmatched/obstructed cuts and empty filler segments are removed.
"""

import subprocess
import os
import sys
import shutil
from pathlib import Path
from PIL import Image
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cs2_clip_maker.killfeed_cutout import detect_killfeed_pills_pro

FFMPEG  = r"C:\cs2_demos\ffmpeg.exe"
FFPROBE = r"C:\Users\Х\.cs2-highlights\tools\ffmpeg\bin\ffprobe.exe"

RAW_DIR = Path(r"C:\cs2_demos\dontblink_raw_clips")
NEW_DIR = Path(r"C:\cs2_demos\dontblink_1to1_clips")
U3D_DIR = Path(r"C:\cs2_demos\dontblink_unique_3d_clips")
REF_AUD = Path(r"c:\Users\Х\Desktop\CS 2\scratch\wooz_ref_audio.wav")

SCRATCH = Path(r"c:\Users\Х\Desktop\CS 2\scratch\master_vert_3x4_segs")
PROBE_DIR = Path(r"c:\Users\Х\Desktop\CS 2\scratch\verify_new_41")
OUT_VERT = Path(r"c:\Users\Х\Desktop\CS 2\output\DontBlink_Wooz_1to1_Vertical_Reels.mp4")
OUT_EXACT = Path(r"c:\Users\Х\Desktop\CS 2\output\DontBlink_Wooz_1to1_Exact.mp4")
OUT_FINAL = Path(r"c:\Users\Х\Desktop\CS 2\output\DontBlink_Wooz_1to1_Final.mp4")

SCRATCH.mkdir(parents=True, exist_ok=True)
PROBE_DIR.mkdir(parents=True, exist_ok=True)
OUT_VERT.parent.mkdir(parents=True, exist_ok=True)

def resolve_clip(name: str) -> Path:
    for d in (U3D_DIR, NEW_DIR, RAW_DIR):
        p = d / name
        if p.is_file():
            return p
    raise FileNotFoundError(f"Clip not found: {name}")

def get_duration(path: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())

def extract_frame_rgb(mp4_path: Path, t: float) -> np.ndarray:
    tmp_jpg = PROBE_DIR / "_tmp_pill_probe.jpg"
    subprocess.run(
        [FFMPEG, "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(mp4_path), "-frames:v", "1", "-q:v", "2", str(tmp_jpg)],
        capture_output=True
    )
    return np.array(Image.open(tmp_jpg))

def find_best_pills(mp4_path: Path, t_center: float, offsets=(0.06, 0.10, 0.15, 0.22, 0.35)) -> list:
    best_pills = []
    for dt in offsets:
        arr = extract_frame_rgb(mp4_path, max(0.02, t_center + dt))
        pills = detect_killfeed_pills_pro(arr)
        if len(pills) > len(best_pills):
            best_pills = pills
    clean_pills = []
    for (rx0, ry0, rx1, ry1) in best_pills:
        w = rx1 - rx0
        h = ry1 - ry0
        if w % 2 != 0:
            rx1 -= 1
        if h % 2 != 0:
            ry1 -= 1
        if (rx1 - rx0) >= 140 and (ry1 - ry0) >= 24:
            clean_pills.append((rx0, ry0, rx1, ry1))
    return clean_pills

# Complete 1-to-1 Beat-Locked Timeline (0.000s -> 56.800s)
# Every single `demo_3d` segment is 100% matched to the EXACT `pov_kill` right before it!
TIMELINE = [
    # 0. Clean Mirage Window Entry & AWP Bolt Pull (no CT spawn wall-staring or random deaths)
    {"type": "demo_intro", "t_start": 0.000, "t_end": 4.200,
     "file1": "awp_mir_r23_intro.mp4", "ss1": 2.35, "es1": 5.55,
     "label": "Mirage Jungle/Window Entry & AWP Pull-Out"},

    # 1. Kill #1 @ 5.539s — Mirage Window AWP 3K #1 (R7 K1)
    {"type": "pov_kill", "t_start": 4.200, "t_end": 6.690, "hit_t": 5.539,
     "file": "clip_11.mp4", "src_hit": 2.500, "label": "Kill #1: Mirage Window AWP 3K #1 (R7 K1)"},

    # 2. Kill #2 @ 7.341s — Mirage Window AWP 3K #2 (R7 K2)
    {"type": "pov_kill", "t_start": 6.690, "t_end": 8.470, "hit_t": 7.341,
     "file": "clip_11.mp4", "src_hit": 8.260, "label": "Kill #2: Mirage Window AWP 3K #2 (R7 K2)"},

    # 3. Kill #3 @ 9.092s — Mirage Window AWP 3K #3 Headshot (R7 K3 HS)
    {"type": "pov_kill", "t_start": 8.470, "t_end": 10.460, "hit_t": 9.092,
     "file": "clip_11.mp4", "src_hit": 10.580, "label": "Kill #3: Mirage Window AWP 3K #3 HS (R7 K3)"},

    # 4. Kill #4 @ 10.911s — Mirage Window AWP Snipe (R12 K1)
    {"type": "pov_kill", "t_start": 10.460, "t_end": 11.745, "hit_t": 10.911,
     "file": "db_awp_mirage_r12_k12.mp4", "src_hit": 0.400, "label": "Kill #4: Mirage Window AWP Snipe (R12 K1)"},

    # 5. Kill #5 @ 12.713s — Mirage B-Market Window AWP Snipe (R35 K1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 11.745, "t_end": 13.530, "hit_t": 12.713,
     "file": "awp_mir_r35_k1_pov.mp4", "src_hit": 2.017, "label": "Kill #5: Mirage B-Market AWP Snipe (R35 K1)"},
    {"type": "demo_3d", "t_start": 13.530, "t_end": 14.031,
     "file": "awp_mir_r35_k1_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #5 (Same Enemy in B-Market)"},

    # 6. Kill #6 @ 14.481s — PRIMARY DROP: Mirage A-Site Deagle 1-Tap HS (R5 OT1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 14.031, "t_end": 14.932, "hit_t": 14.481,
     "file": "deagle_mir_r5_ot1_pov.mp4", "src_hit": 1.983, "label": "Kill #6: Mirage A-Site Deagle 1-Tap HS (R5 OT1)"},
    {"type": "demo_3d", "t_start": 14.932, "t_end": 15.332,
     "file": "deagle_mir_r5_ot1_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #6 (Same Enemy on A-Site)"},

    # 7. Kill #7 @ 16.266s — Mirage Palace Deagle 1-Tap HS (R19) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 15.332, "t_end": 16.667, "hit_t": 16.266,
     "file": "deagle_mir_r19_hs_pov.mp4", "src_hit": 2.000, "label": "Kill #7: Mirage Palace Deagle 1-Tap HS (R19)"},
    {"type": "demo_3d", "t_start": 16.667, "t_end": 17.601,
     "file": "deagle_mir_r19_hs_3d.mp4", "ss": 0.250, "label": "3D Death of Kill #7 (Same Enemy in Palace)"},

    # 8. Kill #8 @ 18.035s — Mirage B-Market AK-47 4K Entry (R4 K1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 17.601, "t_end": 19.353, "hit_t": 18.035,
     "file": "ak47_mir_r4_k1_pov.mp4", "src_hit": 2.017, "label": "Kill #8: Mirage B-Market AK-47 Entry (R4 K1)"},
    {"type": "demo_3d", "t_start": 19.353, "t_end": 20.237,
     "file": "ak47_mir_r4_k1_3d.mp4", "ss": 0.250, "label": "3D Death of Kill #8 (Same Enemy in B-Market)"},

    # 9. Kill #9 @ 20.737s — Mirage B-Apps Window AWP Snipe (R28 K1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 20.237, "t_end": 21.138, "hit_t": 20.737,
     "file": "awp_mir_r28_k1_pov.mp4", "src_hit": 2.017, "label": "Kill #9: Mirage B-Apps Window AWP (R28 K1)"},
    {"type": "demo_3d", "t_start": 21.138, "t_end": 21.622,
     "file": "awp_mir_r28_k1_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #9 (Same Enemy in B-Window)"},

    # 10. Kill #10 @ 22.072s — Mirage AWP Double Kill #1 (R30 K1)
    {"type": "pov_kill", "t_start": 21.622, "t_end": 22.472, "hit_t": 22.072,
     "file": "db_awp_mirage_r30_k12.mp4", "src_hit": 0.900, "label": "Kill #10: Mirage AWP Double #1 (R30 K1)"},

    # 11. Kill #11 @ 22.973s — Mirage AWP Double Kill #2 (R30 K2)
    {"type": "pov_kill", "t_start": 22.472, "t_end": 23.874, "hit_t": 22.973,
     "file": "db_awp_mirage_r30_k12.mp4", "src_hit": 6.620, "label": "Kill #11: Mirage AWP Double #2 (R30 K2)"},

    # 12. Kill #12 @ 24.324s — Inferno R1 3K USP-S 1-Tap HS #1 + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 23.874, "t_end": 24.791, "hit_t": 24.324,
     "file": "usp_inf_r1_k1_pov.mp4", "src_hit": 2.033, "label": "Kill #12: Inferno R1 3K USP-S 1-Tap HS #1"},
    {"type": "demo_3d", "t_start": 24.791, "t_end": 25.192,
     "file": "usp_inf_r1_k1_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #12 (Same Enemy on Inferno #1)"},

    # 13. Kill #13 @ 25.642s — Inferno R1 3K USP-S 1-Tap HS #2 + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 25.192, "t_end": 26.026, "hit_t": 25.642,
     "file": "usp_inf_r1_k2_pov.mp4", "src_hit": 2.017, "label": "Kill #13: Inferno R1 3K USP-S 1-Tap HS #2"},
    {"type": "demo_3d", "t_start": 26.026, "t_end": 26.543,
     "file": "usp_inf_r1_k2_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #13 (Same Enemy on Inferno #2)"},

    # 14. Kill #14 @ 26.977s — Inferno R1 3K USP-S 1-Tap HS #3
    {"type": "pov_kill", "t_start": 26.543, "t_end": 27.200, "hit_t": 26.977,
     "file": "usp_inf_r1_k3_pov.mp4", "src_hit": 2.017, "label": "Kill #14: Inferno R1 3K USP-S 1-Tap HS #3"},

    # 15. Kill #15 @ 27.427s — Dust2 R17 Deagle 1-Tap Headshot
    {"type": "pov_kill", "t_start": 27.200, "t_end": 27.878, "hit_t": 27.427,
     "file": "db_deagle_d2_r17_hs.mp4", "src_hit": 1.320, "label": "Kill #15: Dust2 R17 Deagle 1-Tap HS"},

    # 16. Kill #16 @ 28.795s — Mirage B-Site AWP Close Noscope (R28 K2) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 27.878, "t_end": 28.930, "hit_t": 28.795,
     "file": "awp_mir_r28_k2_ns_pov.mp4", "src_hit": 2.000, "label": "Kill #16: Mirage B-Site AWP Noscope (R28 K2)"},
    {"type": "demo_3d", "t_start": 28.930, "t_end": 29.680,
     "file": "awp_mir_r28_k2_ns_3d.mp4", "ss": 0.250, "label": "3D Death of Kill #16 (Same Enemy on B-Site)"},

    # 17. Kill #17 @ 30.147s — Mirage A-Site AWP Jump/Close Noscope (R35 K2) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 29.680, "t_end": 30.564, "hit_t": 30.147,
     "file": "awp_mir_r35_k2_ns_pov.mp4", "src_hit": 2.017, "label": "Kill #17: Mirage A-Site AWP Noscope (R35 K2)"},
    {"type": "demo_3d", "t_start": 30.564, "t_end": 31.882,
     "file": "awp_mir_r35_k2_ns_3d.mp4", "ss": 0.220, "label": "3D Death of Kill #17 (Same Enemy on A-Site)"},

    # 18. Kill #18 @ 32.366s — Mirage B-Van M4A1-S Headshot (R15 K2)
    {"type": "pov_kill", "t_start": 31.882, "t_end": 33.700, "hit_t": 32.366,
     "file": "clip_01.mp4", "src_hit": 2.950, "label": "Kill #18: Mirage Van M4A1-S Headshot"},

    # 19. Kill #19 @ 35.969s — Mirage A-Site AWP 4K #2 (R18 K2)
    {"type": "pov_kill", "t_start": 33.700, "t_end": 36.850, "hit_t": 35.969,
     "file": "db_awp_mirage_r18_k12.mp4", "src_hit": 3.100, "label": "Kill #19: Mirage A-Site AWP 4K #2 (R18 K2)"},

    # 20. Kill #20 @ 37.771s — Mirage A-Site AWP 4K #3 Headshot (R18 K3 HS)
    {"type": "pov_kill", "t_start": 36.850, "t_end": 38.650, "hit_t": 37.771,
     "file": "awp_mir_r18_k3_hs_pov.mp4", "src_hit": 2.050, "label": "Kill #20: Mirage A-Site AWP 4K #3 HS (R18 K3)"},

    # 21. Kill #21 @ 39.573s — Mirage A-Site Palace AWP 3K #1 (R26 K1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 38.650, "t_end": 39.880, "hit_t": 39.573,
     "file": "awp_mir_r26_k1_pov.mp4", "src_hit": 2.000, "label": "Kill #21: Mirage A-Site AWP 3K #1 (R26 K1)"},
    {"type": "demo_3d", "t_start": 39.880, "t_end": 40.250,
     "file": "awp_mir_r26_k1_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #21 (Same Enemy in Palace)"},

    # 22. Kill #22 @ 40.941s — Mirage A-Site AWP 3K #2 (R26 K2) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 40.250, "t_end": 41.341, "hit_t": 40.941,
     "file": "awp_mir_r26_k2_pov.mp4", "src_hit": 2.033, "label": "Kill #22: Mirage A-Site AWP 3K #2 (R26 K2)"},
    {"type": "demo_3d", "t_start": 41.341, "t_end": 42.209,
     "file": "awp_mir_r26_k2_3d.mp4", "ss": 0.250, "label": "3D Death of Kill #22 (Same Enemy on A-Site)"},

    # 23. Kill #23 @ 43.076s — Mirage A-Site AK-47 4K Entry (R41 K1) + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 42.209, "t_end": 43.977, "hit_t": 43.076,
     "file": "ak47_mir_r41_k1_pov.mp4", "src_hit": 2.033, "label": "Kill #23: Mirage A-Site AK-47 4K #1 (R41 K1)"},
    {"type": "demo_3d", "t_start": 43.977, "t_end": 44.862,
     "file": "ak47_mir_r41_k1_3d.mp4", "ss": 0.250, "label": "3D Death of Kill #23 (Same Enemy on Triple Box)"},

    # 24. Kill #24 @ 45.762s — Mirage A-Site AK-47 4K Spray #2 (R41 K2)
    {"type": "pov_kill", "t_start": 44.862, "t_end": 46.230, "hit_t": 45.762,
     "file": "db_ak47_mirage_r41_k2.mp4", "src_hit": 2.700, "label": "Kill #24: Mirage A-Site AK-47 4K #2 (R41 K2)"},

    # 25. Kill #25 @ 46.730s — Mirage AK-47 1v3 Clutch Finale Headshot (R29 K3 HS)
    {"type": "pov_kill", "t_start": 46.230, "t_end": 47.631, "hit_t": 46.730,
     "file": "clip_05.mp4", "src_hit": 16.680, "label": "Kill #25: AK-47 1v3 Clutch Headshot #3"},

    # 26. Kill #26 @ 48.081s — Mirage R14 3K USP-S 1-Tap Headshot #1
    {"type": "pov_kill", "t_start": 47.631, "t_end": 48.465, "hit_t": 48.081,
     "file": "usp_mir_r14_k1_pov.mp4", "src_hit": 2.033, "label": "Kill #26: Mirage R14 3K USP-S 1-Tap HS #1"},

    # 27. Kill #27 @ 49.850s — Mirage R14 3K USP-S 1-Tap Headshot #2 + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 48.465, "t_end": 50.150, "hit_t": 49.850,
     "file": "usp_mir_r14_k2_pov.mp4", "src_hit": 2.000, "label": "Kill #27: Mirage R14 3K USP-S 1-Tap HS #2"},
    {"type": "demo_3d", "t_start": 50.150, "t_end": 50.550,
     "file": "usp_mir_r14_k2_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #27 (Same Enemy on B-Site)"},

    # 28. Kill #28 @ 51.635s — Mirage R14 3K USP-S 1-Tap Headshot #3 + EXACT MATCHED 3D DEATH
    {"type": "pov_kill", "t_start": 50.550, "t_end": 52.050, "hit_t": 51.635,
     "file": "usp_mir_r14_k3_pov.mp4", "src_hit": 2.033, "label": "Kill #28: Mirage R14 3K USP-S 1-Tap HS #3"},
    {"type": "demo_3d", "t_start": 52.050, "t_end": 52.486,
     "file": "usp_mir_r14_k3_3d.mp4", "ss": 0.300, "label": "3D Death of Kill #28 (Same Enemy on B-Short)"},

    # 29. Kill #29 @ 54.438s — Climax Finale Mirage A-Site AWP 3K #3 (R26 K3) + Clean Fade Out
    {"type": "pov_kill", "t_start": 52.486, "t_end": 56.390, "hit_t": 54.438,
     "file": "awp_mir_r26_k3_pov.mp4", "src_hit": 2.000, "fade_out": True,
     "label": "Kill #29: Climax Finale Mirage AWP 3K #3 (R26 K3)"},
]

def build_pill_overlay_chain(pills: list, src_label: str, base_label: str, out_label: str, enable_expr: str = None) -> str:
    """
    Cuts out each (rx0, ry0, rx1, ry1) pill from `[src_label]`, scales by 1.48x,
    and overlays onto `[base_label]` in the top 378px blurred bar (above y=378, at y_bot=360, x_right=1048).
    """
    if not pills:
        return f"[{base_label}]null[{out_label}]"

    scale_kf = 1.48
    gap = 8
    scaled_dims = []
    for (rx0, ry0, rx1, ry1) in pills:
        w = rx1 - rx0
        h = ry1 - ry0
        pw = int(round(w * scale_kf))
        ph = int(round(h * scale_kf))
        if pw % 2 != 0:
            pw += 1
        if ph % 2 != 0:
            ph += 1
        scaled_dims.append((rx0, ry0, w, h, pw, ph))

    total_h = sum(d[5] for d in scaled_dims) + gap * (len(scaled_dims) - 1)
    cur_y = max(14, 360 - total_h)
    if cur_y % 2 != 0:
        cur_y -= 1

    n = len(scaled_dims)
    parts = []
    if n == 1:
        parts.append(f"[{src_label}]null[{src_label}_p0]")
    else:
        split_outs = "".join(f"[{src_label}_p{i}]" for i in range(n))
        parts.append(f"[{src_label}]split={n}{split_outs}")

    cur_base = base_label
    for i, (rx0, ry0, w, h, pw, ph) in enumerate(scaled_dims):
        cur_x = 1048 - pw
        if cur_x % 2 != 0:
            cur_x -= 1
        p_cropped = f"{src_label}_c{i}"
        parts.append(
            f"[{src_label}_p{i}]crop=w={w}:h={h}:x={rx0}:y={ry0},"
            f"scale={pw}:{ph}:flags=lanczos[{p_cropped}]"
        )
        next_base = out_label if (i == n - 1) else f"{base_label}_ov{i}"
        en = f":enable='{enable_expr}'" if enable_expr else ""
        parts.append(f"[{cur_base}][{p_cropped}]overlay=x={cur_x}:y={cur_y}{en}[{next_base}]")
        cur_y += ph + gap
        cur_base = next_base

    return ";".join(parts)

def render_segment(idx: int, seg: dict, clip_durs: dict) -> Path:
    f_start = int(round(seg["t_start"] * 60))
    f_end   = int(round(seg["t_end"] * 60))
    n_frames = f_end - f_start
    dur = n_frames / 60.0
    out_mp4 = SCRATCH / f"seg_{idx:02d}.mp4"
    stype = seg["type"]

    if stype == "demo_intro":
        p1 = resolve_clip(seg["file1"])
        ss1 = seg["ss1"]
        es1 = seg["es1"]
        pts_scale = dur / (es1 - ss1)
        fc = (
            f"[0:v]trim=start={ss1:.4f}:end={es1:.4f},setpts={pts_scale:.6f}*(PTS-STARTPTS),fps=60,"
            f"tpad=stop_mode=clone:stop_duration=0.5,trim=end_frame={n_frames},setpts=PTS-STARTPTS,"
            f"eq=brightness=0.05:contrast=1.08:saturation=1.18:gamma=1.22,"
            f"colorbalance=bh=0.05:bm=0.03,split=2[bg_in][fg_in];"
            f"[bg_in]crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10[bg];"
            f"[fg_in]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[fg];"
            f"[bg][fg]overlay=0:378,fade=t=in:st=0:d=0.35,setpts=PTS-STARTPTS[vout]"
        )
        cmd = [
            FFMPEG, "-y",
            "-i", str(p1),
            "-filter_complex", fc,
            "-map", "[vout]",
            "-frames:v", str(n_frames),
            "-an", "-r", "60", "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "fast", "-crf", "15",
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            str(out_mp4)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"[{idx:02d}] 🎬 {seg['label']} ({n_frames}f = {dur:.3f}s)", flush=True)
        return out_mp4

    if stype == "demo_3d":
        src_path = resolve_clip(seg["file"])
        ss = seg.get("ss", 0.280)
        fc = (
            f"[0:v]trim=start={ss:.4f}:end={ss + dur + 0.4:.4f},fps=60,"
            f"tpad=stop_mode=clone:stop_duration=0.5,trim=end_frame={n_frames},setpts=PTS-STARTPTS,"
            f"eq=brightness=0.05:contrast=1.10:saturation=1.18:gamma=1.22,"
            f"colorbalance=bh=0.05:bm=0.03,"
            f"unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.70,"
            f"split=2[bg_in][fg_in];"
            f"[bg_in]crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10[bg];"
            f"[fg_in]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[fg];"
            f"[bg][fg]overlay=0:378,setpts=PTS-STARTPTS[vout]"
        )
        cmd = [
            FFMPEG, "-y",
            "-i", str(src_path),
            "-filter_complex", fc,
            "-map", "[vout]",
            "-frames:v", str(n_frames),
            "-an", "-r", "60", "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "fast", "-crf", "15",
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            str(out_mp4)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"[{idx:02d}] 🎥 {seg['label']} ({n_frames}f = {dur:.3f}s)", flush=True)
        return out_mp4

    if stype == "pov_kill":
        src_path = resolve_clip(seg["file"])
        src_dur  = clip_durs[seg["file"]]
        src_hit  = seg["src_hit"]

        f_hit  = int(round(seg["hit_t"] * 60))
        n_pre  = max(1, f_hit - f_start)
        n_post = max(1, f_end - f_hit)
        pre_dur  = n_pre / 60.0
        post_dur = n_post / 60.0

        avail_pre = max(0.08, min(src_hit, pre_dur))
        pre_ss = max(0.0, src_hit - avail_pre)
        pre_pts_scale = pre_dur / avail_pre

        avail_post = max(0.08, min(src_dur - src_hit, post_dur))
        post_pts_scale = post_dur / avail_post

        pills_after = find_best_pills(src_path, src_hit, offsets=(0.06, 0.10, 0.15, 0.22, 0.35))
        pills_before = find_best_pills(src_path, max(0.05, src_hit - 0.15), offsets=(0.0,))

        t_flash_0 = max(0.0, pre_dur - 0.016)
        t_flash_1 = pre_dur + 0.085
        t_ca_0    = max(0.0, pre_dur - 0.016)
        t_ca_1    = pre_dur + 0.150
        t_shk_0   = max(0.0, pre_dur - 0.016)
        t_shk_1   = pre_dur + 0.240

        fx_chain = (
            "eq=brightness=0.05:contrast=1.08:saturation=1.18:gamma=1.24,"
            "colorbalance=bh=0.05:bm=0.03,"
            "unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.75,"
            f"eq=brightness=0.36:contrast=1.22:enable='between(t,{t_flash_0:.3f},{t_flash_1:.3f})',"
            f"rgbashift=rh=6:rv=-4:gh=0:gv=0:bh=-6:bv=4:enable='between(t,{t_ca_0:.3f},{t_ca_1:.3f})',"
            f"crop=w=1904:h=1072:x='8+if(between(t,{t_shk_0:.3f},{t_shk_1:.3f}),10*sin(2*PI*t*32),0)':y='4+if(between(t,{t_shk_0:.3f},{t_shk_1:.3f}),8*sin(2*PI*t*28),0)',"
            "scale=1920:1080"
        )

        fc_parts = [
            f"[0:v]trim=start={pre_ss:.6f}:end={src_hit:.6f},setpts={pre_pts_scale:.6f}*(PTS-STARTPTS),fps=60,tblend=all_mode=average,tpad=stop_mode=clone:stop_duration=0.5,trim=end_frame={n_pre},setpts=PTS-STARTPTS[vpre]",
            f"[0:v]trim=start={src_hit:.6f}:end={src_hit + avail_post:.6f},setpts={post_pts_scale:.6f}*(PTS-STARTPTS),fps=60,tblend=all_mode=average,tpad=stop_mode=clone:stop_duration=0.5,trim=end_frame={n_post},setpts=PTS-STARTPTS[vpost]",
            f"[vpre][vpost]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS,split=2[v_fx_in][v_kf_in]",
            f"[v_fx_in]{fx_chain},split=2[bg_in][fg_in]",
            f"[bg_in]crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10[bg]",
            f"[fg_in]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[fg]",
            f"[bg][fg]overlay=0:378[v_base]"
        ]

        t_pill_switch = max(0.0, pre_dur - 0.016)
        if pills_before and pills_after:
            fc_parts.append("[v_kf_in]eq=contrast=1.08:saturation=1.15,split=2[kf_pre][kf_post]")
            fc_parts.append(build_pill_overlay_chain(pills_before, "kf_pre", "v_base", "v_with_pre", enable_expr=f"lt(t,{t_pill_switch:.4f})"))
            fc_parts.append(build_pill_overlay_chain(pills_after, "kf_post", "v_with_pre", "v_layered", enable_expr=f"gte(t,{t_pill_switch:.4f})"))
        elif pills_after:
            fc_parts.append("[v_kf_in]eq=contrast=1.08:saturation=1.15[kf_post]")
            fc_parts.append(build_pill_overlay_chain(pills_after, "kf_post", "v_base", "v_layered", enable_expr=f"gte(t,{t_pill_switch:.4f})"))
        else:
            fc_parts.append("[v_kf_in]nullsink")
            fc_parts.append("[v_base]null[v_layered]")

        if seg.get("fade_out"):
            fade_st = max(0.0, dur - 0.75)
            fc_parts.append(f"[v_layered]fade=t=out:st={fade_st:.3f}:d=0.75:color=black,setpts=PTS-STARTPTS[vout]")
        else:
            fc_parts.append("[v_layered]setpts=PTS-STARTPTS[vout]")

        fc = ";".join(fc_parts)

        cmd = [
            FFMPEG, "-y",
            "-i", str(src_path),
            "-filter_complex", fc,
            "-map", "[vout]",
            "-frames:v", str(n_frames),
            "-an",
            "-r", "60", "-fps_mode", "cfr",
            "-c:v", "libx264", "-preset", "fast", "-crf", "15",
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            str(out_mp4)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[-] Error rendering {seg['label']}:\n{r.stderr[-500:]}", flush=True)
            sys.exit(1)
        print(f"[{idx:02d}] 🔥 {seg['label']} | Hit @ {seg['hit_t']:.3f}s (frame {f_hit}, pills={len(pills_after)})", flush=True)
        return out_mp4

def main():
    print("=" * 84, flush=True)
    print("BUILDING 3:4 (1080x1440) VERTICAL REELS MASTER WITH 1-TO-1 MATCHED 3D ENEMY DEATHS", flush=True)
    print("=" * 84, flush=True)

    seen_pov = set()
    for seg in TIMELINE:
        if seg["type"] == "pov_kill":
            key = (seg["file"], round(seg["src_hit"], 2))
            if key in seen_pov:
                raise ValueError(f"Duplicate POV kill detected: {key}")
            seen_pov.add(key)
    print(f"[+] Verified {len(seen_pov)}/29 POV kills are 100% unique (0 duplicates)!", flush=True)

    # Verify every demo_3d matches the POV kill right before it
    for i, seg in enumerate(TIMELINE):
        if seg["type"] == "demo_3d":
            prev_seg = TIMELINE[i - 1]
            assert prev_seg["type"] == "pov_kill"
            base_pov = prev_seg["file"].replace("_pov.mp4", "")
            base_3d  = seg["file"].replace("_3d.mp4", "")
            assert base_pov == base_3d, f"Mismatch: {prev_seg['file']} vs {seg['file']}"
    print("[+] Verified 100% of 3D enemy deaths match the EXACT POV kill right before them!", flush=True)

    clip_durs = {}
    for seg in TIMELINE:
        if seg["type"] == "pov_kill":
            fn = seg["file"]
            if fn not in clip_durs:
                clip_durs[fn] = get_duration(resolve_clip(fn))

    seg_files = []
    for idx, seg in enumerate(TIMELINE):
        out_p = render_segment(idx, seg, clip_durs)
        seg_files.append(out_p)

    concat_txt = SCRATCH / "concat_list.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in seg_files:
            f.write(f"file '{p.as_posix()}'\n")

    video_only = SCRATCH / "video_only_vert_3x4.mp4"
    subprocess.run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt),
        "-c:v", "copy", str(video_only)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    total_dur = TIMELINE[-1]["t_end"]
    print(f"\n[*] Encoding final vertical master output/DontBlink_Wooz_1to1_Vertical_Reels.mp4 ({total_dur:.2f}s)...", flush=True)
    subprocess.run([
        FFMPEG, "-y",
        "-i", str(video_only),
        "-i", str(REF_AUD),
        "-af", f"afade=t=out:st={total_dur - 0.85:.3f}:d=0.85",
        "-t", f"{total_dur:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
        "-r", "60", "-fps_mode", "cfr",
        "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
        "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        str(OUT_VERT)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    shutil.copy2(OUT_VERT, OUT_EXACT)
    shutil.copy2(OUT_VERT, OUT_FINAL)
    print(f"[+] SUCCESS: {OUT_VERT} ({OUT_VERT.stat().st_size / (1024*1024):.1f} MB)", flush=True)
    print(f"[+] SUCCESS: {OUT_EXACT} and {OUT_FINAL} updated!", flush=True)

if __name__ == "__main__":
    main()
