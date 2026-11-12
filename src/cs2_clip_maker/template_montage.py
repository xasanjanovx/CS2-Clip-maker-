"""Automated 1-to-1 Template Fragmovie Montage pipeline for CS2 Clip Maker.

Given a reference montage video or YouTube/Shorts URL (`--template`) and CS2 demo files (`--demos`):
1. Downloads the reference video/audio (if URL) and detects beat/shot impact timestamps (`hit_t`).
2. Parses all demos and selects 100% unique top-tier kills for `--player` (3K/4K/Ace, USP-S/Deagle
   1-tap headshots, AWP multi-kills/noscopes, 1vX clutches — zero duplicate moments).
3. Records tick-exact 1st-person POV kills (`spec_mode 1`) AND 1-to-1 matched 3rd-person victim
   deaths (`spec_mode 2` on the exact victim slot at `kill_tick`) via HLAE + `server.dll`.
4. Renders a zero-drift 60fps CFR vertical 9:16 (1080x1920) Reels montage in the user's signature layout:
   - Center gameplay window: 4:3 stretched vertically to 1080x1124 at y=378..1502 (matching z0T2CTj0SfU)
   - Top (y=0..378) and bottom (y=1502..1920) bars: blurred gameplay background (`boxblur=30:8`, NO black bars)
   - Top killfeed: cut-out red-bordered killfeed pills (`detect_killfeed_pills_pro`) at y_bot=360, x_right=1048
   - 1-to-1 matched 3rd-person enemy deaths immediately following their corresponding POV kill
   - Beat-locked impact flash, RGB split (`rgbashift`), and recoil micro-shake, with no fake headshot SFX.
"""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import shutil
import subprocess
import wave
import numpy as np

from cs2_clip_maker.highlights.config import load_config
from cs2_clip_maker.highlights.recording import hlae, plugin, toolchain
from cs2_clip_maker.highlights.recording.recorder import resolve_tools
from cs2_clip_maker.highlights.util.process import kill_processes
from cs2_clip_maker.highlights.demo.parser import ensure_dem_files
from cs2_clip_maker.highlights.video.ffmpeg import probe_duration, probe_media
from cs2_clip_maker.killfeed_cutout import build_pill_overlay_chain, find_best_pills


def resolve_template_video(template_arg: str, work_dir: Path) -> Path:
    """Resolve a local template MP4 path or download a YouTube/Shorts URL via yt-dlp."""
    p = Path(template_arg)
    if p.is_file():
        return p.resolve()
    if template_arg.startswith(("http://", "https://", "www.")):
        out_mp4 = work_dir / "reference_template.mp4"
        if out_mp4.is_file() and out_mp4.stat().st_size > 100_000:
            return out_mp4
        cmd = [
            "yt-dlp",
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "-o",
            str(out_mp4),
            template_arg,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not out_mp4.is_file():
            raise RuntimeError(f"Failed to download template URL {template_arg}: {res.stderr[-300:]}")
        return out_mp4
    raise FileNotFoundError(f"Template video not found: {template_arg}")


def extract_template_audio_and_beats(ffmpeg_exe: Path, template_mp4: Path, work_dir: Path) -> tuple[Path, list[dict]]:
    """
    Extract 48kHz stereo WAV from template video and detect beat/shot transient peaks (`hit_t`),
    returning `(audio_wav_path, timeline_segments)`.
    """
    wav_path = work_dir / "template_audio.wav"
    subprocess.run(
        [
            str(ffmpeg_exe),
            "-y",
            "-i",
            str(template_mp4),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(wav_path),
        ],
        check=True,
        capture_output=True,
    )

    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if wf.getnchannels() == 2:
        samples = samples.reshape(-1, 2).mean(axis=1)

    total_dur = len(samples) / float(sr)
    hop = int(sr * 0.01)  # 10ms hop
    win = int(sr * 0.04)  # 40ms window
    n_steps = max(1, (len(samples) - win) // hop)
    rms = np.zeros(n_steps, dtype=np.float32)
    for i in range(n_steps):
        seg = samples[i * hop : i * hop + win]
        rms[i] = float(np.sqrt(np.mean(seg * seg) + 1e-8))

    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    thresh = float(np.percentile(onset, 88))
    min_gap_steps = int(0.65 / 0.01)  # min 0.65s between kills

    peaks: list[float] = []
    last_idx = -min_gap_steps
    for i in range(250, n_steps - 150):  # skip first 2.5s intro and last 1.5s outro
        if onset[i] >= thresh and onset[i] == np.max(onset[max(0, i - 15) : min(n_steps, i + 16)]):
            if (i - last_idx) >= min_gap_steps:
                peaks.append(round(i * 0.01, 3))
                last_idx = i

    if not peaks:
        peaks = [round(t, 3) for t in np.arange(3.0, max(5.0, total_dur - 1.5), 1.8)]

    segments: list[dict] = []
    cur_t = 0.0
    for k_idx, hit_t in enumerate(peaks):
        next_hit = peaks[k_idx + 1] if (k_idx + 1) < len(peaks) else min(total_dur, hit_t + 2.2)
        gap_after = next_hit - hit_t
        use_3d = (gap_after >= 1.35) and (k_idx % 2 == 1)
        if use_3d:
            pov_end = round(hit_t + min(0.55, gap_after * 0.38), 3)
            d3_end = round(hit_t + min(1.05, gap_after * 0.72), 3)
            segments.append({
                "type": "pov_kill",
                "t_start": cur_t,
                "t_end": pov_end,
                "hit_t": hit_t,
                "has_3d_after": True,
                "d3_end": d3_end,
            })
            cur_t = d3_end
        else:
            pov_end = round(hit_t + gap_after * 0.55, 3) if (k_idx + 1) < len(peaks) else round(min(total_dur, hit_t + 2.0), 3)
            segments.append({
                "type": "pov_kill",
                "t_start": cur_t,
                "t_end": pov_end,
                "hit_t": hit_t,
                "has_3d_after": False,
                "fade_out": (k_idx == len(peaks) - 1),
            })
            cur_t = pov_end

    return wav_path, segments


def select_unique_best_kills(demo_paths: list[Path], player: str | None, needed_count: int) -> list[dict]:
    """
    Parse demos via demoparser2 and rank all kills by `player` so that:
    - Warmup and knife rounds are excluded.
    - 3K/4K/Ace rounds, USP-S/Deagle 1-tap headshots, AWP multi-kills/noscopes, and clutches
      are prioritized first.
    - Every selected kill has a 100% unique `(demo_path, kill_tick)` and exact `attacker_slot`
      and `victim_slot` (`user_id + 1`) for 1-to-1 POV + 3D recording.
    """
    from demoparser2 import DemoParser

    all_candidates: list[dict] = []
    for dem in demo_paths:
        parser = DemoParser(str(dem))
        deaths = parser.parse_event("player_death", player=["user_id", "team_num"], other=["total_rounds_played", "is_warmup_period"])
        if deaths is None or len(deaths) == 0:
            continue

        target_sid: str | None = None
        for _, row in deaths.iterrows():
            att_name = str(row.get("attacker_name") or "")
            att_sid = str(row.get("attacker_steamid") or "")
            if player is None or player.lower() in att_name.lower() or player == att_sid:
                target_sid = att_sid
                break
        if not target_sid:
            continue

        by_round: dict[int, list[dict]] = defaultdict(list)
        for _, row in deaths.iterrows():
            if bool(row.get("is_warmup_period")):
                continue
            if str(row.get("attacker_steamid") or "") != target_sid:
                continue
            wpn = str(row.get("weapon") or "").lower()
            if "knife" in wpn or "bayonet" in wpn:
                continue
            rnd = int(row.get("total_rounds_played") or 0) + 1
            tick = int(row.get("tick") or 0)
            att_uid = row.get("attacker_user_id")
            vic_uid = row.get("user_user_id")
            if att_uid is None or vic_uid is None or np.isnan(att_uid) or np.isnan(vic_uid):
                continue
            by_round[rnd].append({
                "demo_path": str(dem.resolve()),
                "demo_name": dem.stem,
                "round": rnd,
                "kill_tick": tick,
                "weapon": wpn,
                "headshot": bool(row.get("headshot")),
                "noscope": bool(row.get("noscope")),
                "attacker_slot": int(att_uid) + 1,
                "victim_slot": int(vic_uid) + 1,
                "victim_name": str(row.get("user_name") or "Enemy"),
            })

        for rnd, r_kills in by_round.items():
            r_kills.sort(key=lambda x: x["kill_tick"])
            k_count = len(r_kills)
            hs_count = sum(1 for k in r_kills if k["headshot"])
            for idx_in_r, k in enumerate(r_kills, 1):
                wpn = k["weapon"]
                is_pistol_hs = k["headshot"] and any(p in wpn for p in ("usp", "deagle", "glock", "p250"))
                score = (
                    k_count * 35.0
                    + hs_count * 12.0
                    + (30.0 if is_pistol_hs else 0.0)
                    + (25.0 if k["noscope"] else 0.0)
                    + (18.0 if "awp" in wpn else 0.0)
                    + (15.0 if k["headshot"] else 0.0)
                )
                k["round_kills"] = k_count
                k["kill_index_in_round"] = idx_in_r
                k["score"] = score
                all_candidates.append(k)

    # Sort by round highlight score descending, keeping multi-kill sequences grouped in chronological order
    all_candidates.sort(key=lambda x: (-x["score"], x["demo_name"], x["round"], x["kill_tick"]))
    selected = all_candidates[:needed_count]
    if not selected:
        raise RuntimeError(f"No matching kills found for player={player!r} across {len(demo_paths)} demo(s).")
    return selected


def discover_existing_clip_pairs(clips_dir: Path, needed_count: int) -> tuple[list[dict], dict[str, dict[str, Path]]]:
    """
    Zero-CS2 mode: Discover pre-recorded `<base>_pov.mp4` (and matching `<base>_3d.mp4`) or `.mp4`
    clips inside `clips_dir` so the entire montage can be rendered WITHOUT launching `cs2.exe`.
    """
    pov_files = sorted(clips_dir.glob("*_pov.mp4"))
    if not pov_files:
        pov_files = [p for p in sorted(clips_dir.glob("*.mp4")) if not p.stem.endswith("_3d")]
    if not pov_files:
        raise RuntimeError(f"No .mp4 clips found in --clips-dir: {clips_dir}")

    selected_kills: list[dict] = []
    pair_files: dict[str, dict[str, Path]] = {}
    for idx in range(needed_count):
        pov = pov_files[idx % len(pov_files)]
        base = pov.stem[:-4] if pov.stem.endswith("_pov") else pov.stem
        d3 = clips_dir / f"{base}_3d.mp4"
        pid = f"cached_{idx:02d}_{base}"
        selected_kills.append({"pair_id": pid, "src_hit": 2.017})
        pair_files[pid] = {"pov": pov, "3d": d3}
    return selected_kills, pair_files


def record_pov_and_3d_pairs(
    selected_kills: list[dict],
    work_dir: Path,
    *,
    kill_cs2: bool = True,
    background: bool = True,
    clips_dir_override: Path | None = None,
) -> dict[str, dict[str, Path]]:
    """
    Record tick-exact `<key>_pov.mp4` (`spec_mode 1` on `attacker_slot`) and `<key>_3d.mp4`
    (`spec_mode 2` on `victim_slot`) for each selected kill using CS2 + HLAE + `server.dll`
    in offscreen background mode (`-sw -noborder -x -32000 -y -32000` + `engine_no_focus_sleep 0`
    + `snd_mute_losefocus 0`) so the CS2 window never pops up on the user's screen.
    If clips already exist in `clips_dir`, CS2 is NOT launched at all (`0` cs2.exe processes).
    """
    clips_dir = (clips_dir_override or (work_dir / "recorded_pairs")).resolve()
    clips_dir.mkdir(parents=True, exist_ok=True)

    by_demo: dict[str, list[dict]] = defaultdict(list)
    for idx, k in enumerate(selected_kills):
        k["pair_id"] = f"k{idx:02d}_r{k['round']}_{k['kill_tick']}"
        by_demo[k["demo_path"]].append(k)

    result_map: dict[str, dict[str, Path]] = {}

    for dem_str, items in by_demo.items():
        dem_path = Path(dem_str)
        needed = [
            it for it in items
            if not (clips_dir / f"{it['pair_id']}_pov.mp4").is_file()
            or not (clips_dir / f"{it['pair_id']}_3d.mp4").is_file()
        ]
        if needed:
            config = load_config()
            tools = resolve_tools(config, auto_install=True)
            jobs: list[tuple[int, str, int, int, int, int]] = []
            for it in items:
                kt = it["kill_tick"]
                jobs.append((kt - 128, f"{it['pair_id']}_pov", kt - 128, kt + 112, 1, it["attacker_slot"]))
                jobs.append((kt - 24, f"{it['pair_id']}_3d", kt - 24, kt + 104, 2, it["victim_slot"]))
            jobs.sort(key=lambda x: x[0])

            rec_base = work_dir / f"hlae_{dem_path.stem}"
            rec_base.mkdir(parents=True, exist_ok=True)
            rec_base_cmd = str(rec_base).replace("\\", "/")

            actions: list[dict] = [{
                "tick": 32,
                "commands": [
                    "sv_cheats 1",
                    "engine_no_focus_sleep 0",
                    "snd_mute_losefocus 0",
                    "cl_draw_only_deathnotices 1",
                    "demo_ui_mode 0",
                    "cl_trueview_show_status 0",
                    "r_show_build_info 0",
                    "cl_showfps 0",
                    "snd_musicvolume 0",
                    "volume 0.85",
                    "mirv_streams record screenEnabled 1",
                    "mirv_streams record startMovieWav 1",
                    f'mirv_streams record name "{rec_base_cmd}"',
                    f"demo_gototick {max(64, jobs[0][2] - 128)}",
                ],
            }]

            for idx_j, (_, name, st, et, smode, slot) in enumerate(jobs):
                actions.append({
                    "tick": st - 20,
                    "commands": [
                        "engine_no_focus_sleep 0",
                        "snd_mute_losefocus 0",
                        "cl_draw_only_deathnotices 1",
                        "demo_ui_mode 0",
                        "cl_trueview_show_status 0",
                        f"spec_mode {smode}",
                        f"spec_player {slot}",
                    ],
                })
                actions.append({
                    "tick": st,
                    "commands": [
                        f"spec_mode {smode}",
                        f"spec_player {slot}",
                        f'mirv_streams record name "{rec_base_cmd}/{name}"',
                        "mirv_streams record start",
                    ],
                })
                end_cmds = ["mirv_streams record end"]
                if idx_j + 1 < len(jobs):
                    next_st = jobs[idx_j + 1][2]
                    if (next_st - et) > 192:
                        end_cmds.append(f"demo_gototick {next_st - 96}")
                else:
                    end_cmds.append("quit")
                actions.append({"tick": et, "commands": end_cmds})

            json_path = Path(str(dem_path) + ".json")
            json_path.write_text(json.dumps({"actions": actions}, indent=2), encoding="utf-8")

            if kill_cs2:
                kill_processes("cs2.exe")

            plugin.install_plugin(tools.cs2_folder, tools.plugin_dll)
            try:
                hlae.run_game_with_hlae(
                    hlae_exe=tools.hlae_exe,
                    cs2_exe=tools.cs2_exe,
                    demo_path=dem_path,
                    width=1920,
                    height=1080,
                    timeout_seconds=420,
                    kill_existing=kill_cs2,
                    background=background,
                )
            finally:
                plugin.uninstall_plugin(tools.cs2_folder)
                if json_path.is_file():
                    json_path.unlink(missing_ok=True)

            for _, name, *_ in jobs:
                sub = rec_base / name
                takes = sorted(sub.glob("take*")) if sub.is_dir() else []
                if not takes:
                    continue
                v_mp4 = takes[-1] / "video.mp4"
                a_wav = takes[-1] / "audio.wav"
                out_mp4 = clips_dir / f"{name}.mp4"
                if v_mp4.is_file() and a_wav.is_file():
                    subprocess.run(
                        [
                            str(tools.ffmpeg_exe), "-y",
                            "-i", str(v_mp4), "-i", str(a_wav),
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                            str(out_mp4),
                        ],
                        capture_output=True,
                    )
                elif v_mp4.is_file():
                    shutil.copy2(v_mp4, out_mp4)

        for it in items:
            pid = it["pair_id"]
            result_map[pid] = {
                "pov": clips_dir / f"{pid}_pov.mp4",
                "3d": clips_dir / f"{pid}_3d.mp4",
            }

    return result_map


def render_template_montage(
    *,
    template: str,
    demo_inputs: list[Path] | None = None,
    clips_dir: Path | None = None,
    output_mp4: Path,
    player: str | None = None,
    work_dir: Path | None = None,
    kill_cs2: bool = True,
    background: bool = True,
    no_cs2: bool = False,
    crf: int = 16,
) -> dict:
    """Run the full 1-to-1 template montage pipeline and output the vertical 9:16 Reels MP4."""
    config = load_config()
    ffmpeg_exe = toolchain.find_ffmpeg(config) or toolchain.install_ffmpeg()

    base_work = (work_dir or (output_mp4.parent / f"work_{output_mp4.stem}")).resolve()
    base_work.mkdir(parents=True, exist_ok=True)

    template_mp4 = resolve_template_video(template, base_work)
    wav_path, beat_segments = extract_template_audio_and_beats(ffmpeg_exe, template_mp4, base_work)

    if no_cs2 or (clips_dir and not demo_inputs):
        target_clips_dir = (clips_dir or (base_work / "recorded_pairs")).resolve()
        selected_kills, pair_files = discover_existing_clip_pairs(target_clips_dir, len(beat_segments))
    else:
        if not demo_inputs:
            raise ValueError("Provide --demos (or --clips-dir for Zero-CS2 mode).")
        demo_paths = ensure_dem_files(demo_inputs, base_work / "demos")
        selected_kills = select_unique_best_kills(demo_paths, player, len(beat_segments))
        pair_files = record_pov_and_3d_pairs(
            selected_kills,
            base_work,
            kill_cs2=kill_cs2,
            background=background,
            clips_dir_override=clips_dir,
        )

    segs_dir = base_work / "rendered_segments"
    segs_dir.mkdir(parents=True, exist_ok=True)
    seg_outputs: list[Path] = []

    for idx, bseg in enumerate(beat_segments):
        kill_info = selected_kills[idx % len(selected_kills)]
        pid = kill_info["pair_id"]
        pov_mp4 = pair_files[pid]["pov"]
        d3_mp4 = pair_files[pid]["3d"]
        src_dur = probe_duration(ffmpeg_exe, pov_mp4)
        src_hit = 2.017

        f_start = int(round(bseg["t_start"] * 60))
        f_end = int(round(bseg["t_end"] * 60))
        f_hit = int(round(bseg["hit_t"] * 60))
        n_frames = max(2, f_end - f_start)
        n_pre = max(1, f_hit - f_start)
        n_post = max(1, f_end - f_hit)
        pre_dur = n_pre / 60.0
        post_dur = n_post / 60.0
        dur = n_frames / 60.0

        avail_pre = max(0.08, min(src_hit, pre_dur))
        pre_ss = max(0.0, src_hit - avail_pre)
        pre_pts_scale = pre_dur / avail_pre
        avail_post = max(0.08, min(max(0.2, src_dur - src_hit), post_dur))
        post_pts_scale = post_dur / avail_post

        pills_after = find_best_pills(ffmpeg_exe, pov_mp4, src_hit, base_work)
        pills_before = find_best_pills(ffmpeg_exe, pov_mp4, max(0.05, src_hit - 0.15), base_work, offsets=(0.0,))

        t_flash_0 = max(0.0, pre_dur - 0.016)
        t_flash_1 = pre_dur + 0.085
        t_ca_0 = max(0.0, pre_dur - 0.016)
        t_ca_1 = pre_dur + 0.150
        t_shk_0 = max(0.0, pre_dur - 0.016)
        t_shk_1 = pre_dur + 0.240

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
            "[vpre][vpost]concat=n=2:v=1:a=0,setpts=PTS-STARTPTS,split=2[v_fx_in][v_kf_in]",
            f"[v_fx_in]{fx_chain},split=2[bg_in][fg_in]",
            "[bg_in]crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10[bg]",
            "[fg_in]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[fg]",
            "[bg][fg]overlay=0:378[v_base]",
        ]

        t_pill_switch = max(0.0, pre_dur - 0.016)
        if pills_before and pills_after:
            fc_parts.append("[v_kf_in]eq=contrast=1.08:saturation=1.15,split=2[kf_pre][kf_post]")
            fc_parts.append(build_pill_overlay_chain(pills_before, "kf_pre", "v_base", "v_with_pre", enable_expr=f"lt(t,{t_pill_switch:.4f})", y_bot=360, x_right=1048, scale_kf=1.48))
            fc_parts.append(build_pill_overlay_chain(pills_after, "kf_post", "v_with_pre", "v_layered", enable_expr=f"gte(t,{t_pill_switch:.4f})", y_bot=360, x_right=1048, scale_kf=1.48))
        elif pills_after:
            fc_parts.append("[v_kf_in]eq=contrast=1.08:saturation=1.15[kf_post]")
            fc_parts.append(build_pill_overlay_chain(pills_after, "kf_post", "v_base", "v_layered", enable_expr=f"gte(t,{t_pill_switch:.4f})", y_bot=360, x_right=1048, scale_kf=1.48))
        else:
            fc_parts.append("[v_kf_in]nullsink")
            fc_parts.append("[v_base]null[v_layered]")

        if bseg.get("fade_out"):
            fade_st = max(0.0, dur - 0.75)
            fc_parts.append(f"[v_layered]fade=t=out:st={fade_st:.3f}:d=0.75:color=black,setpts=PTS-STARTPTS[vout]")
        else:
            fc_parts.append("[v_layered]setpts=PTS-STARTPTS[vout]")

        out_pov = segs_dir / f"seg_{idx:02d}_pov.mp4"
        subprocess.run(
            [
                str(ffmpeg_exe), "-y",
                "-i", str(pov_mp4),
                "-filter_complex", ";".join(fc_parts),
                "-map", "[vout]",
                "-frames:v", str(n_frames),
                "-an", "-r", "60", "-fps_mode", "cfr",
                "-c:v", "libx264", "-preset", "fast", "-crf", "15",
                "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
                str(out_pov),
            ],
            check=True,
            capture_output=True,
        )
        seg_outputs.append(out_pov)

        # Render 1-to-1 matched 3rd-person victim death clip immediately following this POV kill
        if bseg.get("has_3d_after") and d3_mp4.is_file():
            f_d3_start = f_end
            f_d3_end = int(round(bseg["d3_end"] * 60))
            n_d3 = max(2, f_d3_end - f_d3_start)
            dur_d3 = n_d3 / 60.0
            ss_d3 = 0.280
            fc_d3 = (
                f"[0:v]trim=start={ss_d3:.4f}:end={ss_d3 + dur_d3 + 0.4:.4f},fps=60,"
                f"tpad=stop_mode=clone:stop_duration=0.5,trim=end_frame={n_d3},setpts=PTS-STARTPTS,"
                "eq=brightness=0.05:contrast=1.10:saturation=1.18:gamma=1.22,"
                "colorbalance=bh=0.05:bm=0.03,"
                "unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount=0.70,"
                "split=2[bg_in][fg_in];"
                "[bg_in]crop=1400:986:200:0,scale=1080:1920,boxblur=30:8,eq=brightness=-0.10[bg];"
                "[fg_in]crop=1400:986:200:0,scale=1080:1124:flags=lanczos[fg];"
                "[bg][fg]overlay=0:378,setpts=PTS-STARTPTS[vout]"
            )
            out_d3 = segs_dir / f"seg_{idx:02d}_3d.mp4"
            subprocess.run(
                [
                    str(ffmpeg_exe), "-y",
                    "-i", str(d3_mp4),
                    "-filter_complex", fc_d3,
                    "-map", "[vout]",
                    "-frames:v", str(n_d3),
                    "-an", "-r", "60", "-fps_mode", "cfr",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "15",
                    "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
                    str(out_d3),
                ],
                check=True,
                capture_output=True,
            )
            seg_outputs.append(out_d3)

    concat_txt = base_work / "concat_list.txt"
    concat_txt.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_outputs), encoding="utf-8")
    video_only = base_work / "video_only_master.mp4"
    subprocess.run(
        [str(ffmpeg_exe), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c:v", "copy", str(video_only)],
        check=True,
        capture_output=True,
    )

    total_dur = probe_duration(ffmpeg_exe, video_only)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg_exe), "-y",
            "-i", str(video_only),
            "-i", str(wav_path),
            "-af", f"afade=t=out:st={max(0.0, total_dur - 0.85):.3f}:d=0.85",
            "-t", f"{total_dur:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
            "-r", "60", "-fps_mode", "cfr",
            "-color_range", "tv", "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
            "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
            "-shortest", "-movflags", "+faststart",
            str(output_mp4),
        ],
        check=True,
        capture_output=True,
    )

    return {
        "output_mp4": str(output_mp4.resolve()),
        "template": str(template_mp4),
        "kills_used": len(beat_segments),
        "probe": probe_media(ffmpeg_exe, output_mp4),
    }
