"""Build the per-sequence command plan (port of CSDM's create-cs2-video-json-file.ts)."""

from __future__ import annotations

from pathlib import Path

from cs2_clip_maker.highlights.config import RecordingConfig
from cs2_clip_maker.highlights.demo.parser import DemoData
from cs2_clip_maker.highlights.models import Sequence
from cs2_clip_maker.highlights.recording.actions_file import ActionsFile

MANDATORY_COMMANDS = (
    "sv_cheats 1",
    "volume 1",
    "cl_hud_telemetry_frametime_show 0",
    "cl_hud_telemetry_net_misdelivery_show 0",
    "cl_hud_telemetry_ping_show 0",
    "cl_hud_telemetry_serverrecvmargin_graph_show 0",
    "cl_trueview_show_status 0",
    "r_show_build_info 0",
    "mirv_streams record screen enabled 1",
    "cl_demo_predict 0",
)


def to_unix_path(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def build_actions_file(
    demo_path: Path,
    sequences: list[Sequence],
    demo: DemoData,
    recording: RecordingConfig,
    work_dir: Path,
) -> ActionsFile:
    actions = ActionsFile(demo_path)
    tickrate = round(demo.tickrate)

    for i, sequence in enumerate(sequences):
        for command in MANDATORY_COMMANDS:
            actions.add_exec(1, command)
        actions.add_exec(1, f"cl_draw_only_deathnotices {1 if recording.clean_hud else 0}")
        actions.add_exec(1, f"mirv_deathmsg lifetime {recording.death_notices_duration}")
        actions.add_exec(1, "mirv_deathmsg filter clear")

        setup_tick = max(1, sequence.start_tick - tickrate)
        output_folder = f"{to_unix_path(work_dir)}/{sequence.name}"
        preset = f"preset{sequence.number}"

        actions.add_exec(setup_tick, "mirv_streams record startMovieWav 1")
        actions.add_exec(setup_tick, f'mirv_streams record name "{output_folder}"')
        actions.add_exec(setup_tick, "mirv_deathmsg clear")
        actions.add_exec(setup_tick, "spec_show_xray 0")
        actions.add_exec(setup_tick, "mp_display_kill_assists 1")

        preset_args = f"-c:v libx264 -preset ultrafast -pix_fmt yuv420p -crf {recording.crf}"
        actions.add_exec(
            setup_tick,
            f'mirv_streams settings add ffmpeg {preset} "{preset_args} '
            f"{{QUOTE}}{output_folder}\\\\video.mp4{{QUOTE}}\"",
        )
        actions.add_exec(setup_tick, f"mirv_streams record screen settings {preset}")
        actions.add_exec(setup_tick, f"mirv_streams record fps {recording.fps}")

        actions.add_pause_playback(max(1, sequence.start_tick - 4))
        actions.add_go_to_tick(1, max(1, setup_tick - 1))

        missing_slots: list[str] = []
        for camera in sequence.cameras:
            slot = demo.slots.get(camera.steamid)
            if slot is None:
                missing_slots.append(camera.steamid)
                continue
            actions.add_spec_player(camera.tick, slot)
        if missing_slots:
            raise ValueError(
                f"No spec_player slot for steamid(s) {missing_slots} — cannot aim the camera"
            )

        actions.add_exec(setup_tick, "mirv_deathmsg filter clear")
        for steamid in sequence.featured_steamids:
            actions.add_exec(
                setup_tick,
                f"mirv_deathmsg filter add attackerMatch=x{steamid} attackerIsLocal=1 block=0",
            )

        actions.add_exec(sequence.start_tick, "mirv_streams record start")
        actions.add_exec(sequence.end_tick, "mirv_streams record end")

        if i == len(sequences) - 1:
            actions.add_exec(sequence.end_tick + 64, "quit")
        else:
            actions.add_go_to_next_sequence(sequence.end_tick + 64)

    return actions
