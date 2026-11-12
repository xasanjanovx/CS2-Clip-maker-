"""Pillow bottom info HUD card renderer + FFmpeg composition for 9:16, 4:5, 9:16-clean, and 16:9."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import struct

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from cs2_clip_maker.highlights.video.concat import concat_clips, concat_clips_with_fade
from cs2_clip_maker.highlights.video.ffmpeg import probe_duration, probe_media, run_ffmpeg
from cs2_clip_maker.killfeed_cutout import build_pill_overlay_chain, find_best_pills

LAYOUT_DIMENSIONS = {
    "9:16": (1080, 1920),
    "9:16-clean": (1080, 1920),
    "4:5": (1080, 1350),
    "16:9": (1920, 1080),
}

BOLD_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialuni.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

REGULAR_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arialuni.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

SYMBOL_FALLBACK_CANDIDATES = (
    r"C:\Windows\Fonts\seguisym.ttf",
    r"C:\Windows\Fonts\arialuni.ttf",
    r"C:\Windows\Fonts\seguiemj.ttf",
)


def _load_font_chain(paths: tuple[str, ...], size: int) -> list[ImageFont.FreeTypeFont]:
    fonts: list[ImageFont.FreeTypeFont] = []
    seen: set[str] = set()
    for p in (*paths, *SYMBOL_FALLBACK_CANDIDATES):
        if p in seen:
            continue
        if os.path.isfile(p):
            try:
                fonts.append(ImageFont.truetype(p, size))
                seen.add(p)
            except OSError:
                continue
    return fonts


@lru_cache(maxsize=32)
def _read_ttf_cmap(font_path: str) -> frozenset[int]:
    """Read supported Unicode codepoints directly from a TTF/TTC/OTF cmap table."""
    try:
        data = Path(font_path).read_bytes()
    except OSError:
        return frozenset()
    if len(data) < 12:
        return frozenset()
    sfnt = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" and len(data) >= 16 else 0
    if sfnt + 12 > len(data):
        return frozenset()
    num_tables = struct.unpack(">H", data[sfnt + 4 : sfnt + 6])[0]
    cmap_off: int | None = None
    for i in range(num_tables):
        pos = sfnt + 12 + i * 16
        if pos + 16 > len(data):
            break
        tag, _chk, off, _ln = struct.unpack(">4sIII", data[pos : pos + 16])
        if tag == b"cmap":
            cmap_off = off
            break
    if cmap_off is None or cmap_off + 4 > len(data):
        return frozenset()

    num_sub = struct.unpack(">H", data[cmap_off + 2 : cmap_off + 4])[0]
    best12: int | None = None
    best4: int | None = None
    for i in range(num_sub):
        p = cmap_off + 4 + i * 8
        if p + 8 > len(data):
            break
        pid, eid, sub_off = struct.unpack(">HHI", data[p : p + 8])
        abs_off = cmap_off + sub_off
        if abs_off + 2 > len(data):
            continue
        fmt = struct.unpack(">H", data[abs_off : abs_off + 2])[0]
        if fmt == 12 and (pid, eid) in ((3, 10), (0, 4), (0, 3)):
            best12 = abs_off
        elif fmt == 4 and (pid, eid) in ((3, 1), (0, 3), (3, 0)):
            if best4 is None or (pid, eid) == (3, 1):
                best4 = abs_off

    cps: set[int] = set()
    if best12 is not None and best12 + 16 <= len(data):
        n_groups = struct.unpack(">I", data[best12 + 12 : best12 + 16])[0]
        for i in range(n_groups):
            p = best12 + 16 + i * 12
            if p + 12 > len(data):
                break
            sc, ec, _gid = struct.unpack(">III", data[p : p + 12])
            if sc <= 0x10FFFF:
                cps.update(range(sc, min(ec, 0x10FFFF) + 1))
        return frozenset(cps)

    if best4 is not None and best4 + 8 <= len(data):
        seg_count = struct.unpack(">H", data[best4 + 6 : best4 + 8])[0] // 2
        end_pos = best4 + 14
        start_pos = end_pos + seg_count * 2 + 2
        if start_pos + seg_count * 2 <= len(data):
            for i in range(seg_count):
                ec = struct.unpack(">H", data[end_pos + i * 2 : end_pos + i * 2 + 2])[0]
                sc = struct.unpack(">H", data[start_pos + i * 2 : start_pos + i * 2 + 2])[0]
                if sc != 0xFFFF:
                    cps.update(range(sc, ec + 1))
    return frozenset(cps)


def _has_rtl_chars(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        if (0x0590 <= cp <= 0x08FF) or (0xFB50 <= cp <= 0xFDFF) or (0xFE70 <= cp <= 0xFEFF):
            return True
    return False


def _shape_text_bidi(text: str) -> str:
    """Shape Arabic/RTL segments in LTR paragraph order without perturbing normal LTR text."""
    if not text or not _has_rtl_chars(text):
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped, base_dir="L")


def _split_font_runs(
    text: str,
    font_chain: list[ImageFont.FreeTypeFont],
) -> list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]]:
    if not text:
        return []
    if not font_chain:
        return [(text, ImageFont.load_default())]

    cmaps = [_read_ttf_cmap(str(getattr(f, "path", ""))) for f in font_chain]
    runs: list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]] = []
    cur_font = font_chain[0]
    buf: list[str] = []

    for ch in text:
        chosen = font_chain[0]
        if not ch.isspace():
            cp = ord(ch)
            for f, cmap in zip(font_chain, cmaps):
                if not cmap or cp in cmap:
                    chosen = f
                    break
        else:
            chosen = cur_font

        if chosen is cur_font:
            buf.append(ch)
        else:
            if buf:
                runs.append(("".join(buf), cur_font))
            cur_font = chosen
            buf = [ch]
    if buf:
        runs.append(("".join(buf), cur_font))
    return runs


def _measure_runs_width(
    draw: ImageDraw.ImageDraw,
    runs: list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]],
) -> float:
    total = 0.0
    for seg, font in runs:
        try:
            total += float(draw.textlength(seg, font=font))
        except Exception:
            bbox = draw.textbbox((0, 0), seg, font=font)
            total += float(bbox[2] - bbox[0])
    return total


def _draw_runs(
    draw: ImageDraw.ImageDraw,
    x: float,
    y_top: float,
    runs: list[tuple[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]],
    fill: tuple[int, int, int, int],
) -> None:
    if not runs:
        return
    primary = runs[0][1]
    ascent = 0
    if hasattr(primary, "getmetrics"):
        try:
            ascent = int(primary.getmetrics()[0])
        except Exception:
            ascent = 0
    cur_x = float(x)
    for seg, font in runs:
        if ascent > 0 and isinstance(font, ImageFont.FreeTypeFont):
            draw.text((cur_x, y_top + ascent), seg, font=font, fill=fill, anchor="ls")
        else:
            draw.text((cur_x, y_top), seg, font=font, fill=fill)
        try:
            cur_x += float(draw.textlength(seg, font=font))
        except Exception:
            bbox = draw.textbbox((0, 0), seg, font=font)
            cur_x += float(bbox[2] - bbox[0])


def _draw_accent_emblem(draw: ImageDraw.ImageDraw, cx: int, cy: int, accent: str) -> None:
    if accent == "gold":
        # Stylized 3-peak crown polygon in gold
        pts = [
            (cx - 12, cy + 8),
            (cx - 14, cy - 6),
            (cx - 5, cy + 1),
            (cx, cy - 10),
            (cx + 5, cy + 1),
            (cx + 14, cy - 6),
            (cx + 12, cy + 8),
        ]
        draw.polygon(pts, fill=(255, 215, 0, 255))
    else:
        # Crisp cyan lightning/chevron emblem
        pts = [
            (cx + 3, cy - 11),
            (cx - 9, cy + 1),
            (cx - 1, cy + 1),
            (cx - 4, cy + 11),
            (cx + 9, cy - 1),
            (cx + 1, cy - 1),
        ]
        draw.polygon(pts, fill=(100, 235, 255, 255))


def make_info_card(
    badge_text: str,
    subtitle_text: str,
    out_path: Path,
    *,
    width: int = 1080,
    height: int = 1920,
    accent: str = "cyan",
    y_pos: int | None = None,
) -> Path:
    """Render a transparent RGBA PNG with a sleek bottom HUD info card."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Prefer Arial Bold/Regular for Latin+Cyrillic+Arabic, falling back per-run to Segoe Symbol for math/Unicode glyphs
    bold_chain = _load_font_chain(
        (
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\seguisb.ttf",
            r"C:\Windows\Fonts\arialuni.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        34 if width <= 1080 else 32,
    )
    sub_chain = _load_font_chain(
        (
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arialuni.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ),
        25 if width <= 1080 else 24,
    )

    badge_runs = _split_font_runs(_shape_text_bidi(badge_text), bold_chain)
    w1 = int(round(_measure_runs_width(draw, badge_runs)))

    # Trim overly long text cleanly so card always fits canvas width
    max_text_w = min(860, width - 160)
    sub_raw = subtitle_text
    while len(sub_raw) > 12:
        cand_runs = _split_font_runs(_shape_text_bidi(sub_raw), sub_chain)
        if _measure_runs_width(draw, cand_runs) <= max_text_w:
            break
        sub_raw = sub_raw[:-4] + "..."
    sub_runs = _split_font_runs(_shape_text_bidi(sub_raw), sub_chain)
    w2 = int(round(_measure_runs_width(draw, sub_runs)))

    emblem_pad = 44
    box_w = min(max(max(w1 + emblem_pad, w2) + 84, 500), width - 80)
    box_h = 104

    if y_pos is not None:
        y0 = y_pos
    elif height == 1920:
        y0 = 1545
    elif height == 1350:
        y0 = 1030
    else:
        y0 = height - 155

    x0 = (width - box_w) // 2

    is_gold = accent == "gold" or any(tok in badge_text.upper() for tok in ("ЭЙС", "ACE", "1V", "КЛАТЧ", "CLUTCH"))
    border_col = (255, 215, 0, 225) if is_gold else (0, 215, 255, 210)
    badge_col = (255, 225, 70, 255) if is_gold else (105, 235, 255, 255)

    draw.rounded_rectangle(
        [x0, y0, x0 + box_w, y0 + box_h],
        radius=18,
        fill=(12, 15, 24, 232),
        outline=border_col,
        width=2,
    )

    total_title_w = w1 + 36
    title_start_x = x0 + (box_w - total_title_w) // 2
    _draw_accent_emblem(draw, title_start_x + 12, y0 + 34, "gold" if is_gold else "cyan")
    _draw_runs(draw, title_start_x + 36, y0 + 14, badge_runs, fill=badge_col)

    tx2 = x0 + (box_w - w2) // 2
    _draw_runs(draw, tx2, y0 + 58, sub_runs, fill=(228, 232, 242, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), "PNG")
    return out_path


def _build_video_filter(
    aspect: str,
    has_info_card: bool,
    kf_start: float,
    src_width: int = 1920,
    src_height: int = 1080,
    pills: list[tuple[int, int, int, int]] | None = None,
) -> tuple[str, int, int]:
    width, height = LAYOUT_DIMENSIONS[aspect]
    if src_width == 1920 and src_height == 1080:
        norm_chain = ""
        norm_in = "[0:v]"
    else:
        norm_chain = "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[v_norm];"
        norm_in = "[v_norm]"

    if aspect in ("9:16", "9:16-clean"):
        show_kf = aspect == "9:16"
        active_pills = pills if pills else [(1620, 70, 1914, 106)]
        parts = [
            f"{norm_chain}{norm_in}split={'3' if show_kf else '2'}[fg_raw][bg_src]" + ("[kf_src]" if show_kf else "") + ";",
            "[bg_src]crop=1400:986:200:0,scale=1080:1920:flags=bilinear,boxblur=30:8,eq=brightness=-0.10[bg];",
            "[fg_raw]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[game];",
            "[bg][game]overlay=0:378",
        ]
        if show_kf:
            parts.append("[base];")
            parts.append("[kf_src]eq=contrast=1.08:saturation=1.15[kf_clean];")
            parts.append(
                build_pill_overlay_chain(
                    active_pills,
                    "kf_clean",
                    "base",
                    "v_kf_out",
                    enable_expr=f"gte(t,{kf_start:.2f})",
                    y_bot=360,
                    x_right=1048,
                    scale_kf=1.48,
                )
            )
            if has_info_card:
                parts.append(";[v_kf_out][2:v]overlay=0:0:enable='gte(t,0.2)'")
            else:
                parts.append(";[v_kf_out]null")
        else:
            if has_info_card:
                parts.append("[v_card_in];[v_card_in][2:v]overlay=0:0:enable='gte(t,0.2)'")
        parts.append(",fps=60,setsar=1,format=yuv420p[outv]")
        return "".join(parts), width, height

    if aspect == "4:5":
        parts = [
            f"{norm_chain}{norm_in}split=3[fg_raw][bg_src][kf_src];",
            "[bg_src]scale=108:135,boxblur=5:5,scale=1080:1350:flags=bilinear,eq=brightness=-0.16[bg];",
            "[fg_raw]crop=in_w:980:0:0,scale=1080:608:flags=lanczos[game];",
            "[kf_src]crop=560:130:1340:50,scale=840:195:flags=lanczos,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.35:t=2[kf];",
            "[bg][game]overlay=0:371[base];",
            f"[base][kf]overlay=x=(W-w)/2:y=130:enable='gte(t,{kf_start:.2f})'",
        ]
        if has_info_card:
            parts.append("[v_card_in];[v_card_in][2:v]overlay=0:0:enable='gte(t,0.2)'")
        parts.append(",fps=60,setsar=1,format=yuv420p[outv]")
        return "".join(parts), width, height

    # 16:9 widescreen
    parts = [f"{norm_chain}{norm_in}scale=1920:1080:flags=lanczos"]
    if has_info_card:
        parts.append("[v_card_in];[v_card_in][2:v]overlay=0:0:enable='gte(t,0.2)'")
    parts.append(",fps=60,setsar=1,format=yuv420p[outv]")
    return "".join(parts), width, height


def render_manifest_video(
    ffmpeg_exe: Path,
    manifest: dict,
    output_mp4: Path,
    work_dir: Path,
    *,
    aspect: str = "9:16",
    killfeed: bool = True,
    info_cards: bool = True,
    music_path: Path | None = None,
    music_volume: float = 0.24,
    voice_volume: float = 2.5,
    game_volume: float = 1.05,
    transitions: str = "none",
    crf: int = 18,
    strict: bool = False,
) -> dict:
    if aspect not in LAYOUT_DIMENSIONS:
        raise ValueError(f"Unsupported aspect '{aspect}'. Choose: {', '.join(LAYOUT_DIMENSIONS)}")

    effective_aspect = "9:16-clean" if (aspect == "9:16" and not killfeed) else aspect
    width, height = LAYOUT_DIMENSIONS[effective_aspect]

    work_dir.mkdir(parents=True, exist_ok=True)
    cards_dir = work_dir / "cards"
    rendered_dir = work_dir / "rendered_clips"
    cards_dir.mkdir(parents=True, exist_ok=True)
    rendered_dir.mkdir(parents=True, exist_ok=True)

    music_dur = 0.0
    if music_path is not None:
        if not music_path.is_file():
            raise FileNotFoundError(f"Music file not found: {music_path}")
        music_dur = max(1.0, probe_duration(ffmpeg_exe, music_path))

    rendered_paths: list[Path] = []
    skipped: list[dict] = []
    music_offset = 0.0

    all_clips = manifest.get("clips", [])
    for clip_idx, clip in enumerate(all_clips):
        clip_id = clip["clip_id"]
        src_video_str = clip.get("game_clip") or clip.get("raw_video")
        if not src_video_str:
            msg = f"{clip_id}: no recorded clip path in manifest"
            if strict:
                raise RuntimeError(msg)
            skipped.append({"clip_id": clip_id, "reason": msg})
            continue

        src_video = Path(src_video_str)
        if not src_video.is_file() or src_video.stat().st_size < 1024:
            msg = f"{clip_id}: video file missing ({src_video})"
            if strict:
                raise RuntimeError(msg)
            skipped.append({"clip_id": clip_id, "reason": msg})
            continue

        media_info = probe_media(ffmpeg_exe, src_video)
        dur_s = max(0.2, float(media_info["duration_seconds"] or clip.get("duration_seconds") or 5.0))
        has_game_audio = bool(media_info.get("has_audio"))

        voice_wav_str = clip.get("voice_wav")
        voice_wav = Path(voice_wav_str) if voice_wav_str and Path(voice_wav_str).is_file() else None

        card_path: Path | None = None
        if info_cards:
            card_path = make_info_card(
                badge_text=clip.get("badge", clip_id),
                subtitle_text=clip.get("subtitle", ""),
                out_path=cards_dir / f"{clip_id}_card.png",
                width=width,
                height=height,
                accent=clip.get("accent", "cyan"),
            )

        kf_hit_t = float(clip.get("first_kill_offset_seconds", 1.8))
        kf_start = max(0.0, min(1.0, kf_hit_t - 0.15))
        detected_pills = (
            find_best_pills(ffmpeg_exe, src_video, kf_hit_t, work_dir)
            if effective_aspect == "9:16"
            else []
        )

        # Build FFmpeg inputs carefully with deterministic stream indices:
        # 0: src_video
        # 1: voice_wav (or silent anullsrc)
        # 2: card PNG (if info_cards; else omitted)
        # next: music_path (if provided)
        cmd = ["-y", "-i", str(src_video)]
        inputs_audio_game_from_zero = has_game_audio

        if voice_wav is not None:
            cmd += ["-i", str(voice_wav)]
        else:
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

        if card_path is not None:
            cmd += ["-i", str(card_path)]

        music_idx = 3 if card_path is not None else 2
        if music_path is not None:
            cmd += ["-stream_loop", "-1", "-i", str(music_path)]

        extra_game_silence_idx: int | None = None
        if not inputs_audio_game_from_zero:
            extra_game_silence_idx = music_idx + (1 if music_path is not None else 0)
            cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

        video_filter, _, _ = _build_video_filter(
            effective_aspect,
            card_path is not None,
            kf_start,
            src_width=int(media_info.get("width") or 1920),
            src_height=int(media_info.get("height") or 1080),
            pills=detected_pills,
        )

        game_trim = f"atrim=0:{dur_s:.3f},asetpts=PTS-STARTPTS," if not inputs_audio_game_from_zero else ""
        voice_trim = f"atrim=0:{dur_s:.3f},asetpts=PTS-STARTPTS," if voice_wav is None else ""
        game_src_ref = "0:a" if inputs_audio_game_from_zero else f"{extra_game_silence_idx}:a"
        audio_parts = [
            f"[{game_src_ref}]{game_trim}aformat=sample_rates=48000:channel_layouts=stereo,volume={game_volume:.2f}[snd_game];",
            f"[1:a]{voice_trim}aformat=sample_rates=48000:channel_layouts=stereo,highpass=f=100,volume={voice_volume:.2f},alimiter=limit=0.95[snd_voice];",
        ]
        mix_inputs = "[snd_game][snd_voice]"
        mix_n = 2

        if music_path is not None:
            is_first = (clip_idx == 0)
            is_last = (clip_idx == len(all_clips) - 1)
            music_fades = ""
            if is_first:
                music_fades += ",afade=t=in:st=0:d=0.3"
            if is_last:
                fade_out_st = max(0.0, dur_s - 1.2)
                music_fades += f",afade=t=out:st={fade_out_st:.2f}:d=1.2"

            audio_parts.append(
                f"[{music_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                f"atrim=start={music_offset:.2f}:duration={dur_s:.2f},asetpts=PTS-STARTPTS,"
                f"volume={music_volume:.2f}{music_fades}[snd_music];"
            )
            mix_inputs += "[snd_music]"
            mix_n = 3
            music_offset = (music_offset + dur_s) % music_dur

        audio_parts.append(
            f"{mix_inputs}amix=inputs={mix_n}:duration=first:normalize=0:dropout_transition=2,alimiter=limit=0.96[outa]"
        )

        filter_complex = video_filter + ";" + "".join(audio_parts)
        out_clip = rendered_dir / f"{clip_id}_rendered.mp4"

        run_ffmpeg(
            ffmpeg_exe,
            [
                *cmd,
                "-filter_complex",
                filter_complex,
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-r",
                "60",
                "-fps_mode",
                "cfr",
                "-g",
                "60",
                "-keyint_min",
                "60",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(out_clip),
            ],
        )
        clip["rendered_clip"] = str(out_clip.resolve())
        rendered_paths.append(out_clip)

    if not rendered_paths:
        raise RuntimeError("Rendering produced 0 clips — verify recorded clips exist before rendering.")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    if transitions == "fade":
        concat_clips_with_fade(ffmpeg_exe, rendered_paths, output_mp4, crf=crf)
    else:
        concat_clips(ffmpeg_exe, rendered_paths, output_mp4, work_dir)

    final_probe = probe_media(ffmpeg_exe, output_mp4)
    return {
        "output_mp4": str(output_mp4.resolve()),
        "aspect": effective_aspect,
        "killfeed": killfeed,
        "info_cards": info_cards,
        "music": str(music_path.resolve()) if music_path else None,
        "clips_rendered": len(rendered_paths),
        "clips_skipped": skipped,
        "probe": final_probe,
    }
