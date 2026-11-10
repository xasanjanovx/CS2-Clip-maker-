"""Install/uninstall the CS2 server plugin safely (restoring gameinfo.gi in finally)."""

from __future__ import annotations

import shutil
from pathlib import Path

from cs2_clip_maker.highlights.recording.steam import get_gameinfo_path

GAMEINFO_ORIGINAL = "Game\tcsgo"
GAMEINFO_PATCHED = "Game\tcsgo/csdm\n\t\t\tGame\tcsgo"


def _plugin_folder(cs2_folder: Path) -> Path:
    return cs2_folder / "game" / "csgo" / "csdm"


def _backup_path(gameinfo: Path) -> Path:
    return gameinfo.with_name(gameinfo.name + ".backup")


def _log_file(cs2_folder: Path) -> Path:
    return cs2_folder / "game" / "bin" / "win64" / "csdm.log"


def install_plugin(cs2_folder: Path, plugin_dll: Path) -> None:
    bin_folder = _plugin_folder(cs2_folder) / "bin"
    bin_folder.mkdir(parents=True, exist_ok=True)
    _log_file(cs2_folder).unlink(missing_ok=True)
    shutil.copyfile(plugin_dll, bin_folder / "server.dll")

    gameinfo = get_gameinfo_path(cs2_folder)
    content = gameinfo.read_text(encoding="utf-8")
    if "Game\tcsgo/csdm" not in content:
        shutil.copyfile(gameinfo, _backup_path(gameinfo))
        gameinfo.write_text(content.replace(GAMEINFO_ORIGINAL, GAMEINFO_PATCHED, 1), encoding="utf-8")


def uninstall_plugin(cs2_folder: Path) -> None:
    gameinfo = get_gameinfo_path(cs2_folder)
    backup = _backup_path(gameinfo)
    if backup.is_file():
        shutil.copyfile(backup, gameinfo)
        backup.unlink(missing_ok=True)
    elif gameinfo.is_file():
        content = gameinfo.read_text(encoding="utf-8")
        if GAMEINFO_PATCHED in content:
            gameinfo.write_text(content.replace(GAMEINFO_PATCHED, GAMEINFO_ORIGINAL, 1), encoding="utf-8")
        elif "Game\tcsgo/csdm" in content:
            cleaned = [line for line in content.splitlines(keepends=True) if "Game\tcsgo/csdm" not in line]
            gameinfo.write_text("".join(cleaned), encoding="utf-8")
    shutil.rmtree(_plugin_folder(cs2_folder), ignore_errors=True)
