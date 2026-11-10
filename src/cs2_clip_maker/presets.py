"""Preset-based CS2 highlight detection, tight-trimming, HUD badge generation, and manifest building."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from cs2_clip_maker.highlights.analysis.clutch import attach_clutches, detect_clutches
from cs2_clip_maker.highlights.analysis.scoring import score_moments
from cs2_clip_maker.highlights.analysis.selection import MIN_SAFE_TICK
from cs2_clip_maker.highlights.demo.parser import DemoData, ensure_dem_files, parse_demo
from cs2_clip_maker.highlights.demo.players import PlayerNotFound, resolve_player
from cs2_clip_maker.highlights.models import Kill, Moment

PRESETS = ("all", "aces", "clutches", "deagle", "pistols", "1taps", "knife_zeus")

MAP_DISPLAY = {
    "de_dust2": "Dust 2",
    "de_mirage": "Mirage",
    "de_inferno": "Inferno",
    "de_nuke": "Nuke",
    "de_ancient": "Ancient",
    "de_anubis": "Anubis",
    "de_vertigo": "Vertigo",
    "de_overpass": "Overpass",
    "de_train": "Train",
    "cs_office": "Office",
    "cs_italy": "Italy",
}

WEAPON_DISPLAY = {
    "ak47": "AK-47",
    "m4a1": "M4A4",
    "m4a1_silencer": "M4A1-S",
    "m4a1_silencer_off": "M4A1-S",
    "awp": "AWP",
    "deagle": "Deagle",
    "usp_silencer": "USP-S",
    "usp_silencer_off": "USP-S",
    "hkp2000": "P2000",
    "glock": "Glock-18",
    "p250": "P250",
    "fiveseven": "Five-SeveN",
    "tec9": "Tec-9",
    "cz75a": "CZ75-Auto",
    "elite": "Dual Berettas",
    "revolver": "R8 Revolver",
    "ssg08": "SSG 08",
    "sg556": "SG 553",
    "aug": "AUG",
    "galilar": "Galil AR",
    "famas": "FAMAS",
    "mp9": "MP9",
    "mac10": "MAC-10",
    "mp7": "MP7",
    "mp5sd": "MP5-SD",
    "ump45": "UMP-45",
    "p90": "P90",
    "bizon": "PP-Bizon",
    "mag7": "MAG-7",
    "xm1014": "XM1014",
    "nova": "Nova",
    "sawedoff": "Sawed-Off",
    "taser": "Zeus x27",
    "hegrenade": "HE",
    "inferno": "Molotov",
}


def pretty_map_name(raw_map: str) -> str:
    key = (raw_map or "unknown").strip().lower()
    if key in MAP_DISPLAY:
        return MAP_DISPLAY[key]
    cleaned = re.sub(r"^(de|cs|ar)_", "", key)
    return cleaned.replace("_", " ").title() or "Unknown"


def pretty_weapon_name(weapon: str) -> str:
    w = (weapon or "").strip().lower()
    if w in WEAPON_DISPLAY:
        return WEAPON_DISPLAY[w]
    if "knife" in w or w == "bayonet":
        return "Knife"
    return w.replace("_", " ").upper() if w else "Unknown"


def parse_exclude_rounds(specs: list[str] | None) -> list[tuple[str | None, int]]:
    rules: list[tuple[str | None, int]] = []
    for raw in specs or []:
        text = raw.strip()
        if not text:
            continue
        if ":" in text:
            scope, _, r_str = text.rpartition(":")
            try:
                rules.append((scope.strip().lower(), int(r_str.strip())))
            except ValueError as exc:
                raise ValueError(f"Invalid --exclude-round value '{raw}' (expected MAP:ROUND or ROUND)") from exc
        else:
            try:
                rules.append((None, int(text)))
            except ValueError as exc:
                raise ValueError(f"Invalid --exclude-round value '{raw}' (expected MAP:ROUND or ROUND)") from exc
    return rules


def _is_round_excluded(
    rules: list[tuple[str | None, int]],
    map_raw: str,
    map_display: str,
    demo_id: str,
    round_num: int,
) -> bool:
    for scope, target_round in rules:
        if target_round != round_num:
            continue
        if scope is None:
            return True
        if scope in map_raw.lower() or scope in map_display.lower() or scope in demo_id.lower():
            return True
    return False


def _tighten_kills(
    kills: list[Kill],
    tickrate: float,
    tight_gap_sec: float,
    max_action_sec: float | None,
    *,
    must_keep_end: bool = False,
    allow_all_if_ace: bool = False,
) -> list[Kill]:
    if len(kills) <= 1:
        return list(kills)

    ordered = sorted(kills, key=lambda k: k.tick)
    total_span = (ordered[-1].tick - ordered[0].tick) / tickrate
    max_gap = max(
        (ordered[i + 1].tick - ordered[i].tick) / tickrate for i in range(len(ordered) - 1)
    )
    if allow_all_if_ace and len(ordered) >= 5 and max_gap <= max(tight_gap_sec, 25.0) and total_span <= 60.0:
        return ordered

    within_budget = max_action_sec is None or total_span <= max_action_sec
    if max_gap <= tight_gap_sec and within_budget:
        return ordered

    gap_ticks = int(tight_gap_sec * tickrate)
    clusters: list[list[Kill]] = []
    for kill in ordered:
        if clusters and kill.tick - clusters[-1][-1].tick <= gap_ticks:
            clusters[-1].append(kill)
        else:
            clusters.append([kill])

    candidates: list[list[Kill]] = []
    budget_ticks = int(max_action_sec * tickrate) if max_action_sec is not None else None
    for cluster in clusters:
        if budget_ticks is None or (cluster[-1].tick - cluster[0].tick) <= budget_ticks:
            candidates.append(cluster)
            continue
        left = 0
        for right in range(len(cluster)):
            while left < right and (cluster[right].tick - cluster[left].tick) > budget_ticks:
                left += 1
            candidates.append(cluster[left : right + 1])

    last_kill_tick = ordered[-1].tick

    def rank_slice(sl: list[Kill]) -> tuple:
        hs = sum(1 for k in sl if k.headshot)
        ot = sum(1 for k in sl if k.one_tap)
        deag = sum(1 for k in sl if k.is_deagle)
        spec = sum(1 for k in sl if k.is_special)
        span = (sl[-1].tick - sl[0].tick) / tickrate if len(sl) >= 2 else 0.0
        ends_on_climax = 1 if (must_keep_end and sl[-1].tick == last_kill_tick) else 0
        return (ends_on_climax, len(sl), hs + ot + spec * 2 + deag, -span, sl[-1].tick)

    return max(candidates, key=rank_slice)


def _compute_clip_window(
    moment: Moment,
    demo: DemoData,
    lead_in_sec: float,
    lead_out_sec: float,
    tight_trim: bool,
    tight_gap_sec: float,
    max_clip_sec: float,
) -> tuple[list[Kill], int, int]:
    tickrate = demo.tickrate
    lead_in_ticks = int(round(lead_in_sec * tickrate))
    lead_out_ticks = int(round(lead_out_sec * tickrate))
    round_ = demo.round_at(moment.round_index)

    if not moment.kills:
        # 0-kill won clutch (e.g. ninja defuse / time victory)
        assert moment.clutch is not None
        start = moment.clutch.start_tick - lead_in_ticks
        end = moment.clutch.end_tick + lead_out_ticks
        if max_clip_sec > 0:
            max_ticks = int(round(max_clip_sec * tickrate))
            if end - start > max_ticks:
                start = end - max_ticks
        shown_kills: list[Kill] = []
    else:
        won_clutch = moment.clutch is not None and moment.clutch.won
        is_ace = len(moment.kills) >= 5
        max_action_sec: float | None = None
        if max_clip_sec > 0:
            max_action_sec = max(4.0, max_clip_sec - lead_in_sec - lead_out_sec)
        if tight_trim:
            effective_gap = max(tight_gap_sec, 23.0) if (is_ace or won_clutch) else tight_gap_sec
            shown_kills = _tighten_kills(
                moment.kills,
                tickrate,
                effective_gap,
                None if (is_ace and max_clip_sec <= 40.0) else max_action_sec,
                must_keep_end=won_clutch,
                allow_all_if_ace=True,
            )
        else:
            shown_kills = sorted(moment.kills, key=lambda k: k.tick)

        start = shown_kills[0].tick - lead_in_ticks
        end = shown_kills[-1].tick + lead_out_ticks
        if won_clutch and moment.clutch is not None:
            if shown_kills[-1].tick >= moment.clutch.start_tick:
                # Keep defuse/round celebration briefly, never 40s of dead air
                clutch_tail = min(
                    moment.clutch.end_tick + lead_out_ticks,
                    shown_kills[-1].tick + int(4.5 * tickrate),
                )
                end = max(end, clutch_tail)
        effective_max_clip = max(max_clip_sec, 60.0) if (is_ace and len(shown_kills) >= 5 and max_clip_sec > 0) else max_clip_sec
        if effective_max_clip > 0:
            max_ticks = int(round(effective_max_clip * tickrate))
            if end - start > max_ticks:
                # For won clutches keep the ending instead of cutting off the clutch win
                if won_clutch:
                    start = max(start, end - max_ticks)
                else:
                    end = min(end, start + max_ticks)

    if round_ is not None:
        start = max(start, round_.freeze_end_tick)
        end = min(end, round_.end_tick + int(round(3.0 * tickrate)))
    start = max(start, MIN_SAFE_TICK)
    end = min(end, demo.last_tick)
    if end <= start + int(tickrate):
        end = min(demo.last_tick, start + int(2 * tickrate))
    return shown_kills, start, end


def _classify_tags(
    moment: Moment,
    shown_kills: list[Kill],
    tickrate: float,
) -> list[str]:
    tags: list[str] = []
    k_cnt = len(shown_kills)
    hs_cnt = sum(1 for k in shown_kills if k.headshot)
    ot_cnt = sum(1 for k in shown_kills if k.one_tap)
    deagle_cnt = sum(1 for k in shown_kills if k.is_deagle)
    pistol_cnt = sum(1 for k in shown_kills if k.is_pistol)
    kz_cnt = sum(1 for k in shown_kills if k.is_knife or k.is_zeus)
    span_s = (shown_kills[-1].tick - shown_kills[0].tick) / tickrate if k_cnt >= 2 else 0.0

    if k_cnt >= 5:
        tags.append("aces")
    if moment.clutch is not None and moment.clutch.won:
        tags.append("clutches")
    if deagle_cnt >= 2:
        tags.append("deagle")
    if pistol_cnt >= 2 or (moment.round_index in (0, 12) and k_cnt >= 2):
        tags.append("pistols")
    if k_cnt >= 2 and ((ot_cnt >= 2 and hs_cnt >= 2) or (hs_cnt == k_cnt and span_s <= 4.5) or (deagle_cnt >= 2 and hs_cnt >= 2)):
        tags.append("1taps")
    if kz_cnt >= 1:
        tags.append("knife_zeus")
    return tags


def _matches_preset(
    moment: Moment,
    shown_kills: list[Kill],
    tickrate: float,
    preset: str,
    min_kills_override: int | None,
    has_player_filter: bool,
) -> bool:
    tags = _classify_tags(moment, shown_kills, tickrate)
    k_cnt = len(shown_kills)
    hs_cnt = sum(1 for k in shown_kills if k.headshot)
    ot_cnt = sum(1 for k in shown_kills if k.one_tap)
    deagle_cnt = sum(1 for k in shown_kills if k.is_deagle)
    pistol_cnt = sum(1 for k in shown_kills if k.is_pistol)
    kz_cnt = sum(1 for k in shown_kills if k.is_knife or k.is_zeus)
    span_s = (shown_kills[-1].tick - shown_kills[0].tick) / tickrate if k_cnt >= 2 else 0.0
    won_clutch = moment.clutch is not None and moment.clutch.won

    if preset == "aces":
        return "aces" in tags
    if preset == "clutches":
        return won_clutch
    if preset == "deagle":
        needed = min_kills_override if min_kills_override is not None else 2
        return deagle_cnt >= needed
    if preset == "pistols":
        needed = min_kills_override if min_kills_override is not None else 2
        return pistol_cnt >= needed or (moment.round_index in (0, 12) and k_cnt >= needed)
    if preset == "1taps":
        if min_kills_override == 1 or has_player_filter:
            return ot_cnt >= 1 and hs_cnt >= 1 and k_cnt >= (min_kills_override or 1)
        return "1taps" in tags
    if preset == "knife_zeus":
        return kz_cnt >= 1

    # preset == "all"
    if min_kills_override is not None:
        return k_cnt >= min_kills_override or won_clutch or kz_cnt >= 1 or any(k.is_awp_noscope for k in shown_kills)

    if has_player_filter:
        return (
            k_cnt >= 2
            or won_clutch
            or kz_cnt >= 1
            or any(k.is_special for k in shown_kills)
            or (k_cnt == 1 and ot_cnt == 1 and moment.kills[0].is_deagle)
        )

    if k_cnt >= 3:
        return True
    if won_clutch and (k_cnt >= 1 or (moment.clutch is not None and moment.clutch.opponents >= 2)):
        return True
    if kz_cnt >= 1 or any(k.is_awp_noscope for k in shown_kills):
        return True
    if k_cnt >= 2 and (
        (deagle_cnt >= 2 and hs_cnt >= 1)
        or (hs_cnt >= 2 and span_s <= 3.5)
        or ot_cnt >= 2
        or (pistol_cnt >= 2 and hs_cnt == 2 and span_s <= 3.5)
    ):
        return True
    return False


def _rank_score(moment: Moment, shown_kills: list[Kill], tickrate: float, duration_s: float) -> float:
    k_cnt = len(shown_kills)
    hs_cnt = sum(1 for k in shown_kills if k.headshot)
    ot_cnt = sum(1 for k in shown_kills if k.one_tap)
    deagle_cnt = sum(1 for k in shown_kills if k.is_deagle)
    kz_cnt = sum(1 for k in shown_kills if k.is_knife or k.is_zeus)
    span_s = (shown_kills[-1].tick - shown_kills[0].tick) / tickrate if k_cnt >= 2 else 0.0

    score = float(moment.score)
    if k_cnt >= 5:
        score += 18.0
    elif k_cnt == 4:
        score += 8.0
    elif k_cnt == 3:
        score += 3.0
    if moment.clutch and moment.clutch.won:
        score += 5.0 + moment.clutch.opponents * 2.5
    if deagle_cnt >= 2:
        score += 4.5
    if ot_cnt >= 2 or (k_cnt >= 2 and hs_cnt == k_cnt and span_s <= 3.5):
        score += 4.0
    if kz_cnt >= 1:
        score += 5.0
    if duration_s <= 15.0:
        score += 2.5
    elif duration_s > 35.0:
        score -= 2.5
    return round(score, 3)


def build_hud_labels(
    player_name: str,
    map_display: str,
    round_num: int,
    shown_kills: list[Kill],
    clutch_opponents: int | None,
    clutch_won: bool,
    tickrate: float,
    lang: str = "ru",
) -> tuple[str, str, str]:
    """Return (badge_text, subtitle_text, accent_tier) in RU or EN."""
    k_cnt = len(shown_kills)
    hs_cnt = sum(1 for k in shown_kills if k.headshot)
    ot_cnt = sum(1 for k in shown_kills if k.one_tap)
    span_s = (shown_kills[-1].tick - shown_kills[0].tick) / tickrate if k_cnt >= 2 else 0.0

    weapon_counts: dict[str, int] = defaultdict(int)
    for k in shown_kills:
        weapon_counts[k.weapon] += 1
    ordered_weapons = sorted(weapon_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    primary_weapon_raw = ordered_weapons[0][0] if ordered_weapons else ""
    primary_weapon = pretty_weapon_name(primary_weapon_raw)
    weapon_summary = " + ".join(pretty_weapon_name(w) for w, _ in ordered_weapons[:2])

    has_knife = any(k.is_knife for k in shown_kills)
    has_zeus = any(k.is_zeus for k in shown_kills)
    has_deagle = any(k.is_deagle for k in shown_kills)
    is_pistol_only = k_cnt > 0 and all(k.is_pistol for k in shown_kills)
    all_awp = k_cnt > 0 and all(k.weapon.lower() == "awp" for k in shown_kills)

    accent = "gold" if (k_cnt >= 5 or (clutch_won and (clutch_opponents or 0) >= 2) or has_knife or has_zeus) else "cyan"

    if lang == "en":
        if k_cnt >= 5 and clutch_won and clutch_opponents:
            badge = f"1v{clutch_opponents} ACE CLUTCH ({primary_weapon})"
        elif k_cnt >= 5:
            badge = f"5K ACE ({primary_weapon})"
        elif clutch_won and clutch_opponents:
            if span_s <= 6.5 and k_cnt >= 3:
                badge = f"1v{clutch_opponents} CLUTCH IN {span_s:.1f}S"
            else:
                badge = f"1v{clutch_opponents} CLUTCH ({primary_weapon})"
        elif has_knife and k_cnt >= 2:
            badge = f"{k_cnt}K WITH KNIFE FINISH"
        elif has_knife or has_zeus:
            badge = "ZEUS / KNIFE HIGHLIGHT" if has_zeus else "KNIFE KILL"
        elif k_cnt == 2 and has_deagle and hs_cnt == 2:
            badge = f"2 DEAGLE 1-TAPS ({span_s:.1f}S)"
        elif k_cnt == 2 and hs_cnt == 2 and span_s <= 3.5:
            badge = f"2 FAST 1-TAPS ({span_s:.1f}S)"
        elif has_deagle and k_cnt >= 3:
            badge = f"{k_cnt}K DEAGLE ({hs_cnt} HS)"
        elif is_pistol_only and k_cnt >= 3:
            badge = f"PISTOL {k_cnt}K ({primary_weapon})"
        elif all_awp and k_cnt >= 3:
            badge = f"{k_cnt}K AWP SNIPER"
        elif k_cnt >= 3 and span_s <= 3.5:
            badge = f"{k_cnt}K SPRAY TRANSFER ({span_s:.1f}S)"
        else:
            badge = f"{k_cnt}K {primary_weapon}" if k_cnt > 0 else "CLUTCH"

        detail_parts = [f"{player_name}", f"{map_display} (R{round_num})"]
        if hs_cnt >= 2:
            detail_parts.append(f"{hs_cnt} Headshots")
        elif ot_cnt >= 1 and k_cnt <= 2:
            detail_parts.append("Clean 1-Taps")
        elif weapon_summary:
            detail_parts.append(weapon_summary)
        sub = " • ".join(detail_parts)
        return badge, sub, accent

    # Russian default HUD cards
    if k_cnt >= 5 and clutch_won and clutch_opponents:
        badge = f"1v{clutch_opponents} ЭЙС КЛАТЧ ({primary_weapon})"
    elif k_cnt >= 5:
        badge = f"5K ЭЙС ({primary_weapon})"
    elif clutch_won and clutch_opponents:
        if span_s <= 6.5 and k_cnt >= 3:
            badge = f"1v{clutch_opponents} КЛАТЧ ЗА {span_s:.1f} СЕК"
        else:
            badge = f"1v{clutch_opponents} КЛАТЧ ({primary_weapon})"
    elif has_knife and k_cnt >= 2:
        badge = f"{k_cnt}K + ФИНИШ НА НОЖ"
    elif has_zeus:
        badge = f"{k_cnt}K С ZEUS x27" if k_cnt >= 2 else "УБИЙСТВО С ZEUS x27"
    elif has_knife:
        badge = "КРАСИВЫЙ НОЖ"
    elif k_cnt == 2 and has_deagle and hs_cnt == 2:
        badge = f"2 ВАН-ТАПА С DEAGLE"
    elif k_cnt == 2 and hs_cnt == 2 and span_s <= 3.5:
        badge = f"2 БЫСТРЫХ 1-ТАПА ({span_s:.1f} СЕК)"
    elif has_deagle and k_cnt >= 3:
        badge = f"{k_cnt}K С ДИГЛА ({hs_cnt} ХЕДШОТА)" if hs_cnt in (2, 3, 4) else f"{k_cnt}K С ДИГЛА"
    elif is_pistol_only and k_cnt >= 3:
        badge = f"ПИСТОЛЕТКА: {k_cnt}K ({primary_weapon})"
    elif all_awp and k_cnt >= 3:
        badge = f"{k_cnt}K СО СНАЙПЕРКИ AWP"
    elif k_cnt >= 3 and span_s <= 3.5:
        badge = f"{k_cnt}K БЫСТРЫЙ СПРЕЙ ({span_s:.1f} СЕК)"
    elif k_cnt >= 3:
        badge = f"{k_cnt}K С {primary_weapon}"
    else:
        badge = f"{k_cnt}K • {primary_weapon}" if k_cnt > 0 else f"1v{clutch_opponents or 1} КЛАТЧ"

    detail_parts = [f"{player_name}", f"{map_display} (Раунд {round_num})"]
    if hs_cnt == k_cnt and k_cnt >= 2:
        detail_parts.append(f"{hs_cnt} Хедшота" if hs_cnt in (2, 3, 4) else f"{hs_cnt} Хедшотов")
    elif hs_cnt >= 2:
        detail_parts.append(f"{hs_cnt} HS")
    elif ot_cnt >= 1 and k_cnt <= 2:
        detail_parts.append("Чистые ван-тапы")
    elif weapon_summary:
        detail_parts.append(weapon_summary)
    sub = " • ".join(detail_parts)
    return badge, sub, accent


def analyze_demos_to_manifest(
    demo_inputs: list[Path],
    work_dir: Path,
    *,
    preset: str = "all",
    limit: int = 0,
    player: str | None = None,
    min_kills: int | None = None,
    max_clip_sec: float = 34.0,
    lead_in_sec: float = 2.0,
    lead_out_sec: float = 1.6,
    tight_trim: bool = True,
    tight_gap_sec: float = 18.0,
    include_knife_rounds: bool = False,
    exclude_rounds: list[str] | None = None,
    lang: str = "ru",
) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"Unsupported preset '{preset}'. Choose one of: {', '.join(PRESETS)}")

    exclude_rules = parse_exclude_rounds(exclude_rounds)
    dem_paths = ensure_dem_files(demo_inputs, work_dir)

    candidates: list[dict] = []
    demos_summary: list[dict] = []

    for demo_idx, dem_path in enumerate(dem_paths, 1):
        demo = parse_demo(dem_path)
        demo_id = dem_path.stem
        map_disp = pretty_map_name(demo.map_name)

        target_steamid: str | None = None
        if player:
            try:
                target_steamid = resolve_player(demo, player)
            except PlayerNotFound:
                # If scanning multiple demos, player might only exist in one demo
                if len(dem_paths) == 1:
                    raise
                demos_summary.append(
                    {
                        "demo_id": demo_id,
                        "demo_path": str(dem_path),
                        "map": demo.map_name,
                        "map_display": map_disp,
                        "tickrate": demo.tickrate,
                        "rounds": len(demo.rounds),
                        "skipped_reason": f"Player '{player}' not present in this demo",
                    }
                )
                continue

        demos_summary.append(
            {
                "demo_id": demo_id,
                "demo_path": str(dem_path),
                "map": demo.map_name,
                "map_display": map_disp,
                "tickrate": demo.tickrate,
                "rounds": len(demo.rounds),
                "warmup_rounds_excluded": sorted(r + 1 for r in demo.warmup_rounds),
                "knife_rounds_excluded": sorted(r + 1 for r in demo.knife_rounds)
                if not (preset == "knife_zeus" and include_knife_rounds)
                else [],
            }
        )

        # Group all non-warmup kills at (round_index, steamid) granularity first so
        # full-round Aces/4Ks aren't fragmented before tight-trimming evaluates them.
        by_player_round: dict[tuple[str, int], list[Kill]] = defaultdict(list)
        for kill in demo.kills:
            if kill.round_index in demo.warmup_rounds:
                continue
            if (
                kill.round_index in demo.knife_rounds
                and not (preset == "knife_zeus" and include_knife_rounds)
            ):
                continue
            by_player_round[(kill.attacker_steamid, kill.round_index)].append(kill)

        moments: list[Moment] = []
        for (sid, r_idx), r_kills in by_player_round.items():
            r_kills.sort(key=lambda k: k.tick)
            moments.append(
                Moment(
                    steamid=sid,
                    name=demo.player_names.get(sid) or r_kills[0].attacker_name,
                    side=r_kills[0].attacker_side,
                    round_index=r_idx,
                    kills=r_kills,
                )
            )

        clutches = detect_clutches(demo)
        moments = attach_clutches(moments, clutches, demo)
        score_moments(moments, demo)

        for m in moments:
            if m.round_index in demo.warmup_rounds:
                continue
            if (
                m.round_index in demo.knife_rounds
                and not (preset == "knife_zeus" and include_knife_rounds)
            ):
                continue
            if target_steamid and m.steamid != target_steamid:
                continue
            round_num = m.round_index + 1
            if _is_round_excluded(exclude_rules, demo.map_name, map_disp, demo_id, round_num):
                continue

            shown_kills, start_tick, end_tick = _compute_clip_window(
                m,
                demo,
                lead_in_sec=lead_in_sec,
                lead_out_sec=lead_out_sec,
                tight_trim=tight_trim,
                tight_gap_sec=tight_gap_sec,
                max_clip_sec=max_clip_sec,
            )

            if not _matches_preset(
                m,
                shown_kills,
                demo.tickrate,
                preset=preset,
                min_kills_override=min_kills,
                has_player_filter=target_steamid is not None,
            ):
                continue

            slot = demo.slots.get(m.steamid)
            if slot is None:
                continue

            dur_s = round((end_tick - start_tick) / demo.tickrate, 2)
            first_kill_offset = (
                round(max(0.4, (shown_kills[0].tick - start_tick) / demo.tickrate), 2)
                if shown_kills
                else 1.2
            )
            clutch_opp = m.clutch.opponents if m.clutch else None
            clutch_won = bool(m.clutch and m.clutch.won)
            badge, sub, accent = build_hud_labels(
                player_name=m.name,
                map_display=map_disp,
                round_num=round_num,
                shown_kills=shown_kills,
                clutch_opponents=clutch_opp,
                clutch_won=clutch_won,
                tickrate=demo.tickrate,
                lang=lang,
            )
            score = _rank_score(m, shown_kills, demo.tickrate, dur_s)
            tags = _classify_tags(m, shown_kills, demo.tickrate)

            candidates.append(
                {
                    "demo_index": demo_idx,
                    "demo_id": demo_id,
                    "demo_path": str(dem_path),
                    "map": demo.map_name,
                    "map_display": map_disp,
                    "round": round_num,
                    "player": m.name,
                    "steamid": m.steamid,
                    "slot": slot,
                    "tickrate": demo.tickrate,
                    "start_tick": int(start_tick),
                    "end_tick": int(end_tick),
                    "duration_seconds": dur_s,
                    "first_kill_offset_seconds": first_kill_offset,
                    "preset_tags": tags,
                    "kills_total_in_round": m.kill_count,
                    "kills_shown": len(shown_kills),
                    "headshots_shown": sum(1 for k in shown_kills if k.headshot),
                    "one_taps_shown": sum(1 for k in shown_kills if k.one_tap),
                    "weapons": [k.weapon for k in shown_kills],
                    "clutch": (
                        {
                            "opponents": m.clutch.opponents,
                            "won": m.clutch.won,
                            "kills": m.clutch.kills,
                        }
                        if m.clutch
                        else None
                    ),
                    "score": score,
                    "badge": badge,
                    "subtitle": sub,
                    "accent": accent,
                }
            )

    # Sort best highlights first before applying --limit, then assign stable sequential clip IDs
    candidates.sort(
        key=lambda c: (
            c["score"],
            c["kills_shown"],
            c["headshots_shown"],
            -c["duration_seconds"],
        ),
        reverse=True,
    )
    if limit > 0:
        selected = candidates[:limit]
    else:
        selected = candidates

    # Assign clip IDs cleanly (1..N in curated order: top ranked first for viewing impact!)
    clips: list[dict] = []
    for idx, c in enumerate(selected, 1):
        entry = dict(c)
        entry["clip_id"] = f"clip_{idx:02d}"
        entry["order"] = idx
        clips.append(entry)

    return {
        "schema_version": 1,
        "preset": preset,
        "lang": lang,
        "tight_trim": tight_trim,
        "limit_requested": limit,
        "total_candidates_matched": len(candidates),
        "selected_count": len(clips),
        "total_estimated_duration_seconds": round(sum(c["duration_seconds"] for c in clips), 2),
        "demos": demos_summary,
        "clips": clips,
    }


def write_manifest(manifest: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path
