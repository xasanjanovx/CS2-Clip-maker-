"""Multi-subcommand CLI for CS2 Clip Maker (doctor, analyze, record, extract-voice, render, run)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cs2_clip_maker import __version__
from cs2_clip_maker.composer import render_manifest_video
from cs2_clip_maker.highlights.config import app_data_dir, load_config
from cs2_clip_maker.highlights.demo.parser import parse_demo
from cs2_clip_maker.highlights.models import CameraAction, Sequence
from cs2_clip_maker.highlights.recording import steam, toolchain
from cs2_clip_maker.highlights.recording.recorder import record as record_sequences, resolve_tools
from cs2_clip_maker.highlights.video.postprocess import process_clip
from cs2_clip_maker.presets import PRESETS, analyze_demos_to_manifest, write_manifest
from cs2_clip_maker.voice import extract_voices_for_manifest


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _write_json(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_work_dir(explicit: Path | None, suffix: str) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    return (app_data_dir() / "work" / suffix).resolve()


def _run_doctor(output_path: Path, allow_fail: bool = False) -> int:
    config = load_config()
    checks: dict[str, dict] = {}
    errors: list[str] = []
    warnings: list[str] = []

    is_win = sys.platform == "win32"
    checks["platform"] = {"ok": True, "detail": sys.platform}

    steam_folder = steam.get_steam_folder() if is_win else None
    checks["steam_installed"] = {
        "ok": steam_folder is not None if is_win else None,
        "detail": str(steam_folder) if steam_folder else ("Windows registry key missing" if is_win else "non-Windows host"),
    }

    cs2_folder = steam.get_cs2_folder() if is_win else None
    if config.paths.cs2_exe != "auto":
        cs2_exe = Path(config.paths.cs2_exe)
        cs2_folder = steam.cs2_folder_from_exe(cs2_exe)
    else:
        cs2_exe = steam.get_cs2_exe(cs2_folder) if cs2_folder else None
    cs2_ok = bool(cs2_exe is not None and cs2_exe.is_file())
    checks["cs2_installed"] = {
        "ok": cs2_ok,
        "detail": str(cs2_exe) if cs2_exe else "CS2 (App ID 730) not found in Steam library",
    }
    if not cs2_ok:
        errors.append("CS2 installation not found (required for in-game recording)")

    steam_running = steam.is_steam_running() if is_win else False
    checks["steam_running"] = {
        "ok": steam_running,
        "detail": "running" if steam_running else "not running (start Steam before `record`)",
    }
    if not steam_running:
        warnings.append("Steam is currently not running (start Steam before `record`)")

    cs2_running = steam.is_process_running("cs2.exe") if is_win else False
    checks["cs2_running"] = {
        "ok": not cs2_running,
        "detail": "idle (safe)" if not cs2_running else "cs2.exe active (close or pass --kill-cs2)",
    }
    if cs2_running:
        warnings.append("cs2.exe is currently running")

    hlae = toolchain.find_hlae(config)
    checks["hlae"] = {
        "ok": hlae is not None,
        "detail": str(hlae) if hlae else "not yet installed (run `update-tools` or first `record` auto-downloads)",
    }
    if not hlae:
        warnings.append("HLAE not installed yet (run `update-tools`)")

    if hlae and cs2_folder and cs2_folder.is_dir():
        inf = steam.read_steam_inf(cs2_folder)
        hlae_info = toolchain.read_hlae_install_info(hlae)
        cs2_date = hlae_date = None
        try:
            if inf.get("VersionDate"):
                cs2_date = datetime.strptime(inf["VersionDate"], "%b %d %Y").replace(tzinfo=timezone.utc)
            if hlae_info.get("published_at"):
                hlae_date = datetime.fromisoformat(hlae_info["published_at"].replace("Z", "+00:00"))
        except ValueError:
            pass
        if cs2_date and hlae_date:
            compat = hlae_date >= cs2_date
            checks["hlae_vs_cs2_build"] = {
                "ok": compat,
                "detail": f"CS2 {inf.get('PatchVersion', '?')} ({cs2_date:%Y-%m-%d}) vs HLAE {hlae_info.get('tag', '?')} ({hlae_date:%Y-%m-%d})",
            }
            if not compat:
                warnings.append("CS2 build date is newer than cached HLAE metadata; if CS2 crashes on launch, run `update-tools`")
        else:
            checks["hlae_vs_cs2_build"] = {
                "ok": True,
                "detail": f"CS2 {inf.get('PatchVersion', '?')} (VersionDate={inf.get('VersionDate', '?')}), HLAE={hlae}",
            }

    ffmpeg = toolchain.find_ffmpeg(config)
    checks["ffmpeg"] = {
        "ok": ffmpeg is not None,
        "detail": str(ffmpeg) if ffmpeg else "not found (install FFmpeg or run `update-tools`)",
    }
    if not ffmpeg:
        errors.append("FFmpeg not found (required for render/postprocess)")

    plugin_dll = toolchain.find_plugin_dll(config)
    checks["server_plugin"] = {
        "ok": plugin_dll is not None,
        "detail": str(plugin_dll) if plugin_dll else "server.dll missing from vendor/cs2-plugin/",
    }
    if not plugin_dll:
        errors.append("server.dll missing from vendor/cs2-plugin/")

    work_base = app_data_dir()
    work_base.mkdir(parents=True, exist_ok=True)
    free_gb = round(shutil.disk_usage(work_base).free / (1 << 30), 2)
    checks["disk_space_gb"] = {
        "ok": free_gb >= 5.0,
        "detail": f"{free_gb:.2f} GB free at {work_base}",
    }
    if free_gb < 5.0:
        warnings.append(f"Low disk space at {work_base}: {free_gb:.2f} GB free")

    status = "error" if errors else ("warn" if warnings else "ok")
    report = {
        "version": __version__,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
    }
    _write_json(output_path, report)
    print(f"Success! Doctor report ({status}) written to: {output_path}")
    if errors and not allow_fail:
        for err in errors:
            print(f"Error: {err}", file=sys.stderr)
        return 1
    return 0


def _run_update_tools(output_path: Path) -> int:
    hlae = toolchain.install_hlae()
    ffmpeg = toolchain.install_ffmpeg()
    toolchain.write_ffmpeg_ini(hlae, ffmpeg)
    report = {
        "hlae_exe": str(hlae),
        "ffmpeg_exe": str(ffmpeg),
        "install_info": toolchain.read_hlae_install_info(hlae),
    }
    _write_json(output_path, report)
    print(f"Success! Tools updated. Report written to: {output_path}")
    return 0


def _run_analyze(args: argparse.Namespace) -> dict:
    if args.limit < 0:
        raise ValueError("--limit must be >= 0 (use 0 for unlimited / all matches)")
    work_dir = _resolve_work_dir(args.work_dir, "analyze")
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = analyze_demos_to_manifest(
        demo_inputs=[Path(p) for p in args.demos],
        work_dir=work_dir,
        preset=args.preset,
        limit=args.limit,
        player=args.player,
        min_kills=args.min_kills,
        max_clip_sec=args.max_clip_sec,
        lead_in_sec=args.lead_in,
        lead_out_sec=args.lead_out,
        tight_trim=args.tight_trim,
        tight_gap_sec=args.tight_gap_sec,
        include_knife_rounds=args.include_knife_rounds,
        exclude_rounds=args.exclude_round,
        lang=args.lang,
    )
    out_path = Path(args.output).expanduser().resolve()
    write_manifest(manifest, out_path)
    print(
        f"Success! Selected {manifest['selected_count']}/{manifest['total_candidates_matched']} highlight(s) "
        f"(~{manifest['total_estimated_duration_seconds']:.1f}s) -> {out_path}"
    )
    return manifest


def _run_record(manifest: dict, args: argparse.Namespace, output_path: Path) -> dict:
    config = load_config()
    config.recording.fps = args.fps
    config.recording.width = args.width
    config.recording.height = args.height
    config.recording.crf = args.crf

    tools = resolve_tools(config, auto_install=True)
    work_dir = _resolve_work_dir(args.work_dir, f"rec_{output_path.stem}")
    raw_clips_dir = work_dir / "game_clips"
    raw_clips_dir.mkdir(parents=True, exist_ok=True)

    clips = manifest.get("clips", [])
    if not clips:
        raise ValueError("Manifest contains 0 clips to record")

    by_demo: dict[str, list[dict]] = defaultdict(list)
    for clip in clips:
        by_demo[clip["demo_path"]].append(clip)

    recorded_count = 0
    failed_clips: list[dict] = []

    for demo_idx, (demo_path_str, demo_clips) in enumerate(by_demo.items(), 1):
        dem_path = Path(demo_path_str)
        if not dem_path.is_file():
            msg = f"Demo file missing: {dem_path}"
            if args.strict:
                raise FileNotFoundError(msg)
            for c in demo_clips:
                c["record_status"] = f"error: {msg}"
                failed_clips.append({"clip_id": c["clip_id"], "reason": msg})
            continue

        demo_data = parse_demo(dem_path)
        seqs: list[Sequence] = []
        seq_to_clip: dict[int, dict] = {}
        for idx, c in enumerate(demo_clips, 1):
            seq_num = idx
            seqs.append(
                Sequence(
                    number=seq_num,
                    start_tick=int(c["start_tick"]),
                    end_tick=int(c["end_tick"]),
                    cameras=[CameraAction(tick=int(c["start_tick"]), steamid=str(c["steamid"]))],
                    label=c.get("badge", c["clip_id"]),
                    score=float(c.get("score", 0.0)),
                    round_index=max(0, int(c.get("round", 1)) - 1),
                    moments=[],
                )
            )
            seq_to_clip[seq_num] = c

        demo_work_dir = work_dir / f"demo_{demo_idx:02d}_{dem_path.stem[:32]}"
        try:
            outputs = record_sequences(
                demo_data,
                seqs,
                config,
                demo_work_dir,
                kill_existing_cs2=args.kill_cs2,
                keep_actions=args.keep_temp,
            )
        except Exception as exc:
            # Fail loudly for critical environment/CS2/HLAE setup failures
            raise RuntimeError(f"Recording failed for {dem_path.name}: {exc}") from exc

        by_seq = {out.sequence.number: out for out in outputs}
        for seq_num, c in seq_to_clip.items():
            out = by_seq.get(seq_num)
            if out is None:
                msg = "CS2/HLAE produced no video for sequence"
                if args.strict:
                    raise RuntimeError(f"{c['clip_id']}: {msg}")
                c["record_status"] = f"missing: {msg}"
                failed_clips.append({"clip_id": c["clip_id"], "reason": msg})
                continue

            muxed_name = f"{c['clip_id']}_game.mp4"
            muxed = process_clip(tools.ffmpeg_exe, out, raw_clips_dir, overlay=False, filename=muxed_name)
            c["raw_video"] = str(out.video_path.resolve())
            c["raw_audio"] = str(out.audio_path.resolve()) if out.audio_path else None
            c["game_clip"] = str(muxed.resolve())
            c["record_status"] = "ok"
            recorded_count += 1

    if not args.keep_temp:
        for child in work_dir.iterdir():
            if child.is_dir() and child.name.startswith("demo_"):
                shutil.rmtree(child, ignore_errors=True)

    manifest["recording"] = {
        "fps": args.fps,
        "width": args.width,
        "height": args.height,
        "crf": args.crf,
        "recorded_count": recorded_count,
        "failed_count": len(failed_clips),
        "failed_clips": failed_clips,
    }
    _write_json(output_path, manifest)
    print(f"Success! Recorded {recorded_count}/{len(clips)} clip(s) -> {output_path}")
    return manifest


def _run_extract_voice(manifest: dict, args: argparse.Namespace, output_path: Path) -> dict:
    work_dir = _resolve_work_dir(args.work_dir, f"voice_{output_path.stem}")
    updated = extract_voices_for_manifest(manifest, work_dir)
    _write_json(output_path, updated)
    voiced = sum(1 for c in updated.get("clips", []) if c.get("voice_status") == "ok")
    total = len(updated.get("clips", []))
    print(f"Success! Extracted voice for {voiced}/{total} clip(s) -> {output_path}")
    return updated


def _run_render(manifest: dict, args: argparse.Namespace, output_mp4: Path, report_path: Path) -> dict:
    config = load_config()
    ffmpeg_exe = toolchain.find_ffmpeg(config)
    if ffmpeg_exe is None:
        ffmpeg_exe = toolchain.install_ffmpeg()

    work_dir = _resolve_work_dir(args.work_dir, f"render_{output_mp4.stem}")
    music_path = Path(args.music).expanduser().resolve() if args.music else None

    render_info = render_manifest_video(
        ffmpeg_exe=ffmpeg_exe,
        manifest=manifest,
        output_mp4=output_mp4,
        work_dir=work_dir,
        aspect=args.aspect,
        killfeed=args.killfeed,
        info_cards=args.info_cards,
        music_path=music_path,
        music_volume=args.music_volume,
        voice_volume=args.voice_volume,
        game_volume=args.game_volume,
        transitions=args.transitions,
        crf=args.crf,
        strict=args.strict,
    )
    if not args.keep_temp:
        shutil.rmtree(work_dir, ignore_errors=True)

    full_report = {**render_info, "manifest_summary": {
        "preset": manifest.get("preset"),
        "selected_count": manifest.get("selected_count"),
        "clips": manifest.get("clips", []),
    }}
    _write_json(report_path, full_report)
    probe = render_info["probe"]
    print(
        f"Success! Rendered {render_info['clips_rendered']} clip(s) "
        f"({probe['width']}x{probe['height']}, {probe['duration_seconds']:.1f}s) -> {output_mp4}"
    )
    return full_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cs2-clip-maker",
        description="Turn CS2 demo files (.dem, .zip, .dem.gz, .dem.zst) into curated highlight MP4 videos.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- doctor ---
    p_doc = subparsers.add_parser("doctor", help="Validate CS2, Steam, HLAE, FFmpeg, server.dll, and disk space.")
    p_doc.add_argument("--output", required=True, type=Path, help="Output JSON report path")
    p_doc.add_argument("--no-fail", action="store_true", help="Exit 0 even if environment errors are detected")

    # --- update-tools ---
    p_upd = subparsers.add_parser("update-tools", help="Download latest HLAE and FFmpeg into ~/.cs2-clip-maker/tools.")
    p_upd.add_argument("--output", required=True, type=Path, help="Output JSON report path")

    def _add_analyze_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--demos", "--demo", dest="demos", nargs="+", action="extend", required=True, help="One or more .dem, .dem.gz, .dem.zst, .zip paths or folders")
        p.add_argument("--preset", choices=PRESETS, default="all", help="Highlight selection preset")
        p.add_argument("--limit", required=True, type=int, help="Max clips to select (0 = unlimited / all matching highlights)")
        p.add_argument("--player", default=None, help="Filter to a single player name or SteamID64")
        p.add_argument("--min-kills", type=int, default=None, help="Override minimum kills per highlight")
        p.add_argument("--max-clip-sec", type=float, default=34.0, help="Target max duration per clip when tight-trimming (0 = unlimited)")
        p.add_argument("--lead-in", type=float, default=2.0, help="Seconds before first kill")
        p.add_argument("--lead-out", type=float, default=1.6, help="Seconds after last kill")
        p.add_argument("--tight-trim", action=argparse.BooleanOptionalAction, default=True, help="Trim long idle gaps inside rounds")
        p.add_argument("--tight-gap-sec", type=float, default=18.0, help="Split action bursts when gap exceeds this many seconds")
        p.add_argument("--include-knife-rounds", action="store_true", help="Include opening knife rounds (only applies with --preset knife_zeus)")
        p.add_argument("--exclude-round", action="append", default=[], help="Exclude specific round (e.g. 'Dust 2:10' or '10')")
        p.add_argument("--lang", choices=("ru", "en"), default="ru", help="HUD card language (ru or en)")

    # --- analyze ---
    p_ana = subparsers.add_parser("analyze", help="Parse demos, detect highlights, tight-trim gaps, and write manifest JSON.")
    _add_analyze_args(p_ana)
    p_ana.add_argument("--work-dir", type=Path, default=None, help="Working directory for decompressed demos")
    p_ana.add_argument("--output", required=True, type=Path, help="Output manifest JSON path")

    def _add_record_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--fps", type=int, default=60, help="Capture framerate")
        p.add_argument("--width", type=int, default=1920, help="Capture width")
        p.add_argument("--height", type=int, default=1080, help="Capture height")
        p.add_argument("--crf", type=int, default=18, help="x264 CRF quality")
        p.add_argument("--kill-cs2", action="store_true", help="Terminate any already running cs2.exe before recording")
        p.add_argument("--keep-temp", action="store_true", help="Keep temporary HLAE recordings and <demo>.json")
        p.add_argument("--strict", action="store_true", help="Fail if any individual clip fails to record")

    # --- record ---
    p_rec = subparsers.add_parser("record", help="Record manifest clips through CS2 + HLAE + server.dll.")
    p_rec.add_argument("--manifest", required=True, type=Path, help="Input manifest JSON from `analyze`")
    _add_record_args(p_rec)
    p_rec.add_argument("--work-dir", type=Path, default=None, help="Working directory for recorded game clips")
    p_rec.add_argument("--output", required=True, type=Path, help="Output updated manifest JSON path")

    # --- extract-voice ---
    p_voi = subparsers.add_parser("extract-voice", help="Extract and decode in-game Opus team voice chat from .dem for each clip.")
    p_voi.add_argument("--manifest", required=True, type=Path, help="Input manifest JSON")
    p_voi.add_argument("--work-dir", type=Path, default=None, help="Working directory for decoded voice WAVs")
    p_voi.add_argument("--output", required=True, type=Path, help="Output updated manifest JSON path")

    def _add_render_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--aspect", choices=("9:16", "9:16-clean", "4:5", "16:9"), default="9:16", help="Output video layout")
        p.add_argument("--killfeed", action=argparse.BooleanOptionalAction, default=True, help="Overlay enlarged top killfeed crop on 9:16/4:5")
        p.add_argument("--info-cards", action=argparse.BooleanOptionalAction, default=True, help="Overlay bottom HUD highlight cards")
        p.add_argument("--music", type=Path, default=None, help="Optional background music track (.mp3/.wav)")
        p.add_argument("--music-volume", type=float, default=0.24, help="Background music volume multiplier")
        p.add_argument("--voice-volume", type=float, default=2.5, help="Team voice chat gain multiplier")
        p.add_argument("--game-volume", type=float, default=1.05, help="In-game audio volume multiplier")
        p.add_argument("--transitions", choices=("none", "fade"), default="none", help="Clip transition style")

    # --- render ---
    p_ren = subparsers.add_parser("render", help="Render final MP4 with top killfeed, bottom info HUD cards, voice, and music.")
    p_ren.add_argument("--manifest", required=True, type=Path, help="Input manifest JSON with recorded clips")
    _add_render_args(p_ren)
    p_ren.add_argument("--crf", type=int, default=18, help="Output x264 CRF quality")
    p_ren.add_argument("--strict", action="store_true", help="Fail if any clip video is missing")
    p_ren.add_argument("--keep-temp", action="store_true", help="Keep intermediate cards and rendered clip segments")
    p_ren.add_argument("--work-dir", type=Path, default=None, help="Working directory for rendered segments")
    p_ren.add_argument("--output", required=True, type=Path, help="Final output .mp4 file path")
    p_ren.add_argument("--report", type=Path, default=None, help="Optional JSON render report path")

    # --- run ---
    p_run = subparsers.add_parser("run", help="Full pipeline: analyze -> record -> extract-voice -> render.")
    _add_analyze_args(p_run)
    _add_record_args(p_run)
    _add_render_args(p_run)
    p_run.add_argument("--work-dir", type=Path, default=None, help="Job workspace root")
    p_run.add_argument("--output", required=True, type=Path, help="Final output .mp4 file path")
    p_run.add_argument("--report", type=Path, default=None, help="Final JSON report path (defaults to <output>.json)")

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "doctor":
            return _run_doctor(Path(args.output).expanduser().resolve(), allow_fail=args.no_fail)
        if args.command == "update-tools":
            return _run_update_tools(Path(args.output).expanduser().resolve())
        if args.command == "analyze":
            _run_analyze(args)
            return 0
        if args.command == "record":
            manifest = _read_json(Path(args.manifest).expanduser().resolve())
            _run_record(manifest, args, Path(args.output).expanduser().resolve())
            return 0
        if args.command == "extract-voice":
            manifest = _read_json(Path(args.manifest).expanduser().resolve())
            _run_extract_voice(manifest, args, Path(args.output).expanduser().resolve())
            return 0
        if args.command == "render":
            out_mp4 = Path(args.output).expanduser().resolve()
            report = Path(args.report).expanduser().resolve() if args.report else out_mp4.with_suffix(".render.json")
            manifest = _read_json(Path(args.manifest).expanduser().resolve())
            _run_render(manifest, args, out_mp4, report)
            return 0
        if args.command == "run":
            out_mp4 = Path(args.output).expanduser().resolve()
            base_work = _resolve_work_dir(args.work_dir, f"run_{out_mp4.stem}")
            base_work.mkdir(parents=True, exist_ok=True)
            report = Path(args.report).expanduser().resolve() if args.report else out_mp4.with_suffix(".json")

            manifest = analyze_demos_to_manifest(
                demo_inputs=[Path(p) for p in args.demos],
                work_dir=base_work / "analyze",
                preset=args.preset,
                limit=args.limit,
                player=args.player,
                min_kills=args.min_kills,
                max_clip_sec=args.max_clip_sec,
                lead_in_sec=args.lead_in,
                lead_out_sec=args.lead_out,
                tight_trim=args.tight_trim,
                tight_gap_sec=args.tight_gap_sec,
                include_knife_rounds=args.include_knife_rounds,
                exclude_rounds=args.exclude_round,
                lang=args.lang,
            )
            if manifest["selected_count"] == 0:
                raise RuntimeError("No highlights matched the requested filters/preset.")
            write_manifest(manifest, base_work / "01_analyzed.json")

            args_rec = argparse.Namespace(
                fps=args.fps,
                width=args.width,
                height=args.height,
                crf=args.crf,
                kill_cs2=args.kill_cs2,
                keep_temp=args.keep_temp,
                strict=args.strict,
                work_dir=base_work / "record",
            )
            manifest = _run_record(manifest, args_rec, base_work / "02_recorded.json")

            args_voice = argparse.Namespace(work_dir=base_work / "voice")
            manifest = _run_extract_voice(manifest, args_voice, base_work / "03_voiced.json")

            args_ren = argparse.Namespace(
                aspect=args.aspect,
                killfeed=args.killfeed,
                info_cards=args.info_cards,
                music=args.music,
                music_volume=args.music_volume,
                voice_volume=args.voice_volume,
                game_volume=args.game_volume,
                transitions=args.transitions,
                crf=args.crf,
                strict=args.strict,
                keep_temp=args.keep_temp,
                work_dir=base_work / "render",
            )
            _run_render(manifest, args_ren, out_mp4, report)
            return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
