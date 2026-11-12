"""Launch CS2 through HLAE's custom loader."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import psutil

from cs2_clip_maker.highlights.config import app_data_dir
from cs2_clip_maker.highlights.recording.sequences import to_unix_path
from cs2_clip_maker.highlights.util.log import info
from cs2_clip_maker.highlights.util.process import (
    find_processes,
    kill_processes,
    wait_for_process_exit,
    wait_for_process_start,
)

GAME_PROCESS = "cs2.exe"

CRASH_ADVICE = (
    "The game crashed during startup (minidump created). The most common cause is an "
    "HLAE build incompatible with the current CS2 version: CS2 updates regularly break "
    "injection until advancedfx ships a new HLAE release (usually within days). "
    "Run update-tools to fetch the latest HLAE, or check "
    "https://github.com/advancedfx/advancedfx/releases"
)


class HlaeError(RuntimeError):
    pass


class GameError(RuntimeError):
    pass


def find_minidumps_since(cs2_exe: Path, since_timestamp: float) -> list[Path]:
    dumps = []
    for dump in cs2_exe.parent.glob("*.mdmp"):
        try:
            if dump.stat().st_mtime >= since_timestamp:
                dumps.append(dump)
        except OSError:
            continue
    return dumps


def build_hlae_args(
    hlae_exe: Path,
    cs2_exe: Path,
    demo_path: Path,
    width: int,
    height: int,
) -> list[str]:
    game_args = (
        f'-insecure -novid -condebug +playdemo "{to_unix_path(demo_path)}"'
        f" -width {width} -height {height} -sw"
    )
    return [
        str(hlae_exe),
        "-noGui",
        "-autoStart",
        "-noConfig",
        "-afxDisableSteamStorage",
        "-customLoader",
        "-hookDllPath",
        str(hlae_exe.parent / "x64" / "AfxHookSource2.dll"),
        "-programPath",
        str(cs2_exe),
        "-cmdLine",
        game_args,
    ]


def _hlae_error_window_exists() -> bool:
    result = subprocess.run(
        ["tasklist", "/fi", f"imagename eq {GAME_PROCESS}", "/v", "/nh"],
        capture_output=True,
        text=True,
        encoding="cp866",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return "Error - AfxHookSource" in (result.stdout or "")


def run_game_with_hlae(
    hlae_exe: Path,
    cs2_exe: Path,
    demo_path: Path,
    width: int,
    height: int,
    timeout_seconds: float,
    kill_existing: bool = True,
) -> None:
    """Inject, play the demo, and block until cs2.exe exits (or timeout)."""
    if kill_existing and kill_processes(GAME_PROCESS):
        info("Killed a running CS2 instance, waiting for file locks to release...")
        time.sleep(3)

    old_pids = {p.pid for p in find_processes(GAME_PROCESS)}
    cfg_sandbox = app_data_dir() / "cs2-cfg"
    cfg_sandbox.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, USRLOCALCSGO=str(cfg_sandbox))

    launch_time = time.time()
    args = build_hlae_args(hlae_exe, cs2_exe, demo_path, width, height)
    launcher = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    try:
        time.sleep(2)
        if _hlae_error_window_exists():
            raise HlaeError("HLAE injection failed (Error - AfxHookSource window detected)")

        def crash_suffix() -> str:
            dumps = find_minidumps_since(cs2_exe, launch_time)
            return f"\n{CRASH_ADVICE}\nMinidump: {dumps[-1]}" if dumps else ""

        info("Waiting for CS2 to initialize and load assets...")
        init_deadline = time.monotonic() + 60.0
        real_game_pid = None
        while time.monotonic() < init_deadline:
            for p in find_processes(GAME_PROCESS):
                try:
                    rss_mb = p.memory_info().rss / (1024 * 1024)
                    if rss_mb > 600:
                        real_game_pid = p.pid
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if real_game_pid is not None:
                break
            time.sleep(1.0)

        if real_game_pid is None:
            procs = find_processes(GAME_PROCESS)
            if not procs:
                raise GameError(
                    f"CS2 did not start (or crashed immediately; launcher status {launcher.poll()}).{crash_suffix()}"
                )
            real_game_pid = procs[-1].pid

        info(f"CS2 game engine active (PID {real_game_pid}), recording in progress (CS2 closes when done)...")
        consecutive_missing = 0
        record_deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < record_deadline:
            if not psutil.pid_exists(real_game_pid):
                consecutive_missing += 1
                if consecutive_missing >= 5:
                    break
            else:
                consecutive_missing = 0
            time.sleep(1.0)
        else:
            kill_processes(GAME_PROCESS)
            raise GameError(
                f"Recording did not finish within {int(timeout_seconds)}s watchdog — killed CS2"
            )

        dumps = find_minidumps_since(cs2_exe, launch_time)
        if dumps:
            raise GameError(f"CS2 crashed during recording.{crash_suffix()}")
    finally:
        kill_processes(GAME_PROCESS)
        if launcher.poll() is None:
            try:
                launcher.terminate()
                launcher.wait(timeout=5)
            except Exception:
                try:
                    launcher.kill()
                except Exception:
                    pass
