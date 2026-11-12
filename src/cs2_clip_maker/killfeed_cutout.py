"""Alpha-invariant red-bordered CS2 killfeed pill detector and FFmpeg cutout overlay builder."""

from __future__ import annotations

from pathlib import Path
import subprocess
from PIL import Image
import numpy as np


def detect_killfeed_pills_pro(frame_rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """
    Detect local player's red-bordered killfeed pills in a 1920x1080 CS2 frame.
    Uses alpha-invariant vertical R-G ridge contrast along the 2px top and bottom borders
    of each pill (separated by dy in 28..32 px), rejecting non-red teammate/enemy pills
    and orange/red background walls or damage vignettes.
    """
    if frame_rgb.ndim != 3 or frame_rgb.shape[0] < 260 or frame_rgb.shape[1] < 1918:
        return []

    y0_s, y1_s = 64, 260
    x0_s, x1_s = 1350, 1918
    roi = frame_rgb[y0_s:y1_s, x0_s:x1_s].astype(np.int16)
    r, g = roi[:, :, 0], roi[:, :, 1]
    redness = r - g

    h_roi, _ = redness.shape
    pills: list[tuple[int, int, int, int]] = []

    y = 4
    while y < h_roi - 35:
        best_score = 0
        best_dy = 30
        for dy in range(28, 33):
            y_bot = y + dy
            if y_bot >= h_roi - 4:
                break
            top_red = redness[y : y + 2, 320:556].max(axis=0)
            bot_red = redness[y_bot - 1 : y_bot + 1, 320:556].max(axis=0)
            in_red = np.median(redness[y + 4 : y_bot - 3, 320:556], axis=0)
            abv_red = redness[max(0, y - 3) : max(1, y - 1), 320:556].min(axis=0)
            blw_red = redness[y_bot + 2 : min(h_roi, y_bot + 4), 320:556].min(axis=0)

            valid_cols = (
                (bot_red >= 15)
                & ((top_red - in_red) >= 12)
                & ((bot_red - in_red) >= 14)
                & ((top_red - abv_red) >= 8)
                & ((bot_red - blw_red) >= 8)
            )
            score = int(valid_cols.sum())
            if score > best_score:
                best_score = score
                best_dy = dy

        if best_score >= 75:
            y_top = y
            y_bot = y + best_dy
            top_red_f = redness[y_top : y_top + 2, :562].max(axis=0)
            bot_red_f = redness[y_bot - 1 : y_bot + 1, :562].max(axis=0)
            in_red_f = np.median(redness[y_top + 4 : y_bot - 3, :562], axis=0)
            abv_red_f = redness[max(0, y_top - 3) : max(1, y_top - 1), :562].min(axis=0)
            blw_red_f = redness[y_bot + 2 : min(h_roi, y_bot + 4), :562].min(axis=0)

            col_is_pill = (
                ((top_red_f - in_red_f) >= 9)
                & ((bot_red_f - in_red_f) >= 9)
                & (((top_red_f - abv_red_f) >= 6) | ((bot_red_f - blw_red_f) >= 6))
            )

            x_right = 562
            x_left = 360
            gap = 0
            found_any = False
            for x in range(552, 0, -1):
                if col_is_pill[x]:
                    x_left = x
                    found_any = True
                    gap = 0
                elif found_any:
                    gap += 1
                    if gap > 65:
                        break

            if (x_right - x_left) >= 155:
                rx0 = max(0, x0_s + min(x_left, 368) - 4)
                ry0 = max(0, y0_s + y_top - 1)
                rx1 = min(1920, x0_s + x_right + 2)
                ry1 = min(1080, y0_s + y_bot + 2)
                if (rx1 - rx0) % 2 != 0:
                    rx1 -= 1
                if (ry1 - ry0) % 2 != 0:
                    ry1 -= 1
                pills.append((rx0, ry0, rx1, ry1))
            y = y_bot + 2
        else:
            y += 1

    return pills


def extract_frame_rgb(ffmpeg_exe: Path, mp4_path: Path, t: float, tmp_jpg: Path) -> np.ndarray | None:
    tmp_jpg.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg_exe),
            "-y",
            "-ss",
            f"{max(0.0, t):.3f}",
            "-i",
            str(mp4_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(tmp_jpg),
        ],
        capture_output=True,
    )
    if not tmp_jpg.is_file():
        return None
    try:
        return np.array(Image.open(tmp_jpg).convert("RGB"))
    except Exception:
        return None


def find_best_pills(
    ffmpeg_exe: Path,
    mp4_path: Path,
    t_center: float,
    work_dir: Path,
    offsets: tuple[float, ...] = (0.06, 0.10, 0.15, 0.22, 0.35),
) -> list[tuple[int, int, int, int]]:
    best_pills: list[tuple[int, int, int, int]] = []
    tmp_jpg = work_dir / "_tmp_pill_probe.jpg"
    for dt in offsets:
        arr = extract_frame_rgb(ffmpeg_exe, mp4_path, max(0.02, t_center + dt), tmp_jpg)
        if arr is None:
            continue
        pills = detect_killfeed_pills_pro(arr)
        if len(pills) > len(best_pills):
            best_pills = pills
    return best_pills


def build_pill_overlay_chain(
    pills: list[tuple[int, int, int, int]],
    src_label: str,
    base_label: str,
    out_label: str,
    *,
    enable_expr: str | None = None,
    y_bot: int = 360,
    x_right: int = 1048,
    scale_kf: float = 1.48,
) -> str:
    """
    Build an FFmpeg filtergraph chain that cuts out each (rx0, ry0, rx1, ry1) killfeed pill
    from `[src_label]`, scales it by `scale_kf`, and overlays it in the top blurred bar
    above the gameplay window (`y_bot = 360`, right above `y = 378`).
    """
    if not pills:
        return f"[{base_label}]null[{out_label}]"

    gap = 8
    scaled_dims: list[tuple[int, int, int, int, int, int]] = []
    for rx0, ry0, rx1, ry1 in pills:
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
    cur_y = max(14, y_bot - total_h)
    if cur_y % 2 != 0:
        cur_y -= 1

    n = len(scaled_dims)
    parts: list[str] = []
    if n == 1:
        parts.append(f"[{src_label}]null[{src_label}_p0]")
    else:
        split_outs = "".join(f"[{src_label}_p{i}]" for i in range(n))
        parts.append(f"[{src_label}]split={n}{split_outs}")

    cur_base = base_label
    for i, (rx0, ry0, w, h, pw, ph) in enumerate(scaled_dims):
        cur_x = x_right - pw
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
