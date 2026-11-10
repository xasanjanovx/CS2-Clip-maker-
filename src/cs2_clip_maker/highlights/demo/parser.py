"""Parse CS2 demos (.dem, .dem.gz, .dem.zst, .zip) with demoparser2 into plain model objects."""

from __future__ import annotations

import gzip
import math
import shutil
import zipfile
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from demoparser2 import DemoParser

from cs2_clip_maker.highlights.models import PRECISION_WEAPONS, Kill, Round

DEFAULT_TICKRATE = 64.0
_SIDE_BY_TEAM_NUM = {2: "T", 3: "CT"}


@dataclass(frozen=True)
class Death:
    """Unfiltered death record, used for alive-tracking (clutch detection)."""

    tick: int
    round_index: int
    victim_steamid: str


@dataclass
class DemoData:
    path: Path
    map_name: str
    tickrate: float
    kills: list[Kill]  # filtered: no warmup, teamkills, suicides
    deaths: list[Death]  # unfiltered: every player death after match start
    rounds: list[Round]
    player_names: dict[str, str]  # steamid -> name
    slots: dict[str, int]  # steamid -> spec_player slot
    rosters: dict[int, dict[str, str]] = field(default_factory=dict)  # round -> {steamid: side}
    match_start_tick: int = 0
    last_tick: int = 0
    warmup_rounds: set[int] = field(default_factory=set)
    knife_rounds: set[int] = field(default_factory=set)

    def round_at(self, index: int) -> Round | None:
        if 0 <= index < len(self.rounds):
            return self.rounds[index]
        return None


def ensure_dem_file(path: Path, work_dir: Path) -> Path:
    """Decompress .dem.gz or .dem.zst into work_dir; pass .dem through unchanged."""
    suffix = path.suffix.lower()
    if suffix not in (".gz", ".zst"):
        return path
    target = work_dir / path.name[: -len(suffix)]
    if not target.suffix:
        target = target.with_suffix(".dem")
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".gz":
        with gzip.open(path, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        import zstandard

        with open(path, "rb") as src, open(target, "wb") as dst:
            zstandard.ZstdDecompressor().copy_stream(src, dst)
    return target


def ensure_dem_files(inputs: list[Path], work_dir: Path) -> list[Path]:
    """Expand .dem, .dem.gz, .dem.zst, .zip archives, and directories into concrete .dem paths."""
    resolved: list[Path] = []
    seen: set[Path] = set()

    def _add(dem_path: Path) -> None:
        canonical = dem_path.resolve()
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(canonical)

    for item in inputs:
        p = item.expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(f"Demo input not found: {item}")
        if p.is_dir():
            nested = sorted(
                [
                    child
                    for child in p.iterdir()
                    if child.is_file()
                    and child.name.lower().endswith((".dem", ".dem.gz", ".dem.zst", ".zip"))
                ]
            )
            if not nested:
                raise ValueError(f"No CS2 demo files (.dem/.gz/.zst/.zip) found in directory: {p}")
            for sub in ensure_dem_files(nested, work_dir):
                _add(sub)
            continue

        lower = p.name.lower()
        if lower.endswith(".zip"):
            extract_dir = work_dir / "extracted" / p.stem
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(p, "r") as zf:
                members = [
                    m
                    for m in zf.infolist()
                    if not m.is_dir()
                    and Path(m.filename).name.lower().endswith((".dem", ".dem.gz", ".dem.zst"))
                ]
                if not members:
                    raise ValueError(f"Zip archive contains no CS2 demos: {p}")
                for m in members:
                    safe_name = Path(m.filename).name
                    out_file = extract_dir / safe_name
                    if not out_file.is_file() or out_file.stat().st_size == 0:
                        with zf.open(m, "r") as src, open(out_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    _add(ensure_dem_file(out_file, extract_dir))
        elif lower.endswith((".dem.gz", ".dem.zst")):
            extract_dir = work_dir / "extracted"
            _add(ensure_dem_file(p, extract_dir))
        elif lower.endswith(".dem"):
            _add(p)
        else:
            raise ValueError(f"Unsupported demo format: {p.name} (expected .dem, .dem.gz, .dem.zst, or .zip)")
    return resolved


def _normalize_side(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _SIDE_BY_TEAM_NUM.get(int(value), "")
    text = str(value or "").upper()
    if text.startswith("T"):
        return "T"
    if text == "CT":
        return "CT"
    return ""


def _normalize_winner(value: object) -> str:
    return _normalize_side(value)


def _safe_event(parser: DemoParser, name: str, **kwargs) -> pd.DataFrame:
    try:
        df = parser.parse_event(name, **kwargs)
    except Exception:
        return pd.DataFrame()
    if df is None:
        return pd.DataFrame()
    return df


def _col(row: pd.Series, name: str, default=None):
    value = row.get(name, default)
    if pd.isna(value):
        return default
    return value


def _compute_tickrate(parser: DemoParser, sample_ticks: list[int]) -> float:
    if len(sample_ticks) < 2:
        return DEFAULT_TICKRATE
    t1, t2 = min(sample_ticks), max(sample_ticks)
    if t2 - t1 < 256:
        return DEFAULT_TICKRATE
    try:
        df = parser.parse_ticks(["game_time"], ticks=[t1, t2])
    except Exception:
        return DEFAULT_TICKRATE
    if df.empty:
        return DEFAULT_TICKRATE
    grouped = df.groupby("tick")["game_time"].first()
    if len(grouped) < 2:
        return DEFAULT_TICKRATE
    dt = grouped.index.max() - grouped.index.min()
    dgt = grouped.loc[grouped.index.max()] - grouped.loc[grouped.index.min()]
    if dgt <= 0:
        return DEFAULT_TICKRATE
    rate = dt / dgt
    if not math.isfinite(rate) or rate < 16 or rate > 256:
        return DEFAULT_TICKRATE
    return float(round(rate))


def _build_rounds(parser: DemoParser, match_start_tick: int) -> list[Round]:
    starts = _safe_event(parser, "round_start")
    ends = _safe_event(parser, "round_end")
    freeze_ends = _safe_event(parser, "round_freeze_end")

    start_ticks = sorted(int(t) for t in starts.get("tick", pd.Series(dtype=int)))
    freeze_ticks = sorted(int(t) for t in freeze_ends.get("tick", pd.Series(dtype=int)))

    rounds: list[Round] = []
    if ends.empty:
        return rounds
    ends = ends.sort_values("tick")
    for _, row in ends.iterrows():
        end_tick = int(row["tick"])
        if end_tick < match_start_tick:
            continue
        prior_starts = [t for t in start_ticks if t <= end_tick]
        start_tick = prior_starts[-1] if prior_starts else match_start_tick
        freeze_in_round = [t for t in freeze_ticks if start_tick <= t <= end_tick]
        freeze_end_tick = freeze_in_round[-1] if freeze_in_round else start_tick
        rounds.append(
            Round(
                index=len(rounds),
                start_tick=start_tick,
                freeze_end_tick=freeze_end_tick,
                end_tick=end_tick,
                winner=_normalize_winner(row.get("winner")),
            )
        )
    return rounds


def get_slots(parser: DemoParser, sample_ticks: int | list[int]) -> dict[str, int]:
    """steamid -> spec_player slot (slot = demo user_id + 1 for CS2)."""
    ticks = [sample_ticks] if isinstance(sample_ticks, int) else sorted(set(sample_ticks))
    try:
        df = parser.parse_ticks(["user_id"], ticks=ticks)
    except Exception:
        return {}
    if df.empty or "user_id" not in df.columns:
        return {}
    slots: dict[str, int] = {}
    for _, row in df.iterrows():
        steamid = str(row.get("steamid", ""))
        user_id = row.get("user_id")
        if steamid and pd.notna(user_id):
            slots[steamid] = int(user_id) + 1
    return slots


def _build_rosters(parser: DemoParser, rounds: list[Round]) -> dict[int, dict[str, str]]:
    sample_by_tick: dict[int, int] = {}
    for r in rounds:
        sample_by_tick[r.freeze_end_tick + 16] = r.index
    try:
        df = parser.parse_ticks(["is_alive", "team_num"], ticks=list(sample_by_tick.keys()))
    except Exception:
        return {}
    rosters: dict[int, dict[str, str]] = {r.index: {} for r in rounds}
    if df.empty:
        return rosters
    for _, row in df.iterrows():
        tick = int(row["tick"])
        round_index = sample_by_tick.get(tick)
        if round_index is None:
            continue
        if not bool(row.get("is_alive", False)):
            continue
        side = _normalize_side(row.get("team_num"))
        if side not in ("CT", "T"):
            continue
        steamid = str(row.get("steamid", ""))
        if steamid:
            rosters[round_index][steamid] = side
    return rosters


def _build_hurt_index(parser: DemoParser) -> dict[tuple[str, str], list[int]]:
    hurt_df = _safe_event(parser, "player_hurt")
    if hurt_df.empty:
        return {}
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for _, row in hurt_df.iterrows():
        att = str(_col(row, "attacker_steamid", "") or "")
        vic = str(_col(row, "user_steamid", "") or "")
        if att and vic and att != vic:
            index[(att, vic)].append(int(row["tick"]))
    for key in index:
        index[key].sort()
    return index


def parse_demo(path: Path) -> DemoData:
    parser = DemoParser(str(path))

    try:
        header = parser.parse_header()
    except Exception:
        header = {}
    map_name = str(header.get("map_name", "unknown"))

    new_match = _safe_event(parser, "begin_new_match")
    match_start_tick = 0
    if not new_match.empty and "tick" in new_match.columns:
        match_start_tick = int(new_match["tick"].max())

    deaths_df = _safe_event(
        parser,
        "player_death",
        player=["team_name"],
        other=["is_warmup_period"],
    )
    if deaths_df.empty:
        raise ValueError(f"No player_death events found in {path.name} — is this a valid CS2 demo?")
    deaths_df = deaths_df.sort_values("tick")

    rounds = _build_rounds(parser, match_start_tick)
    if not rounds:
        raise ValueError(f"No round_end events found in {path.name} — demo looks incomplete")
    round_start_ticks = [r.start_tick for r in rounds]

    def round_index_for_tick(tick: int) -> int:
        idx = bisect_right(round_start_ticks, tick) - 1
        return max(0, idx)

    temp_deaths: list[Death] = []
    temp_rows: list[dict] = []
    for _, row in deaths_df.iterrows():
        tick = int(row["tick"])
        if tick < match_start_tick:
            continue
        if bool(_col(row, "is_warmup_period", False)):
            continue
        victim_steamid = str(_col(row, "user_steamid", "") or "")
        round_index = round_index_for_tick(tick)
        if victim_steamid:
            temp_deaths.append(Death(tick=tick, round_index=round_index, victim_steamid=victim_steamid))

        attacker_steamid = str(_col(row, "attacker_steamid", "") or "")
        if not attacker_steamid or attacker_steamid == victim_steamid:
            continue
        attacker_side = _normalize_side(_col(row, "attacker_team_name", ""))
        victim_side = _normalize_side(_col(row, "user_team_name", ""))
        if attacker_side and victim_side and attacker_side == victim_side:
            continue
        temp_rows.append(
            {
                "tick": tick,
                "round_index": round_index,
                "attacker_steamid": attacker_steamid,
                "attacker_name": str(_col(row, "attacker_name", "") or ""),
                "attacker_side": attacker_side,
                "victim_steamid": victim_steamid,
                "victim_name": str(_col(row, "user_name", "") or ""),
                "weapon": str(_col(row, "weapon", "") or ""),
                "headshot": bool(_col(row, "headshot", False)),
                "noscope": bool(_col(row, "noscope", False)),
                "penetrated": int(_col(row, "penetrated", 0) or 0),
                "thrusmoke": bool(_col(row, "thrusmoke", False)),
                "attackerblind": bool(_col(row, "attackerblind", False)),
            }
        )

    death_ticks = [d.tick for d in temp_deaths]
    tickrate = _compute_tickrate(parser, death_ticks)
    hurt_index = _build_hurt_index(parser)
    one_tap_window = int(3.0 * tickrate)

    kills: list[Kill] = []
    for item in temp_rows:
        is_one_tap = False
        if item["headshot"] and item["weapon"].lower() in PRECISION_WEAPONS:
            hits = hurt_index.get((item["attacker_steamid"], item["victim_steamid"]), [])
            lo = bisect_left(hits, item["tick"] - one_tap_window)
            hi = bisect_right(hits, item["tick"])
            is_one_tap = (hi - lo) <= 1
        kills.append(Kill(**item, one_tap=is_one_tap))

    # Identify synthetic warmup (>10 deaths) or knife/taser rounds (>=4 knife/zeus kills)
    deaths_per_round: dict[int, int] = defaultdict(int)
    knife_kills_per_round: dict[int, int] = defaultdict(int)
    for d in temp_deaths:
        deaths_per_round[d.round_index] += 1
    for k in kills:
        if k.is_knife or k.is_zeus:
            knife_kills_per_round[k.round_index] += 1

    warmup_rounds = {r_idx for r_idx, cnt in deaths_per_round.items() if cnt > 10}
    knife_rounds = {r_idx for r_idx, cnt in knife_kills_per_round.items() if cnt >= 4}

    player_names: dict[str, str] = {}
    try:
        pinfo = parser.parse_player_info()
        for _, row in pinfo.iterrows():
            steamid = str(row.get("steamid", ""))
            if steamid:
                player_names[steamid] = str(row.get("name", ""))
    except Exception:
        pass
    for kill in kills:
        player_names.setdefault(kill.attacker_steamid, kill.attacker_name)
        player_names.setdefault(kill.victim_steamid, kill.victim_name)

    last_tick = max(
        rounds[-1].end_tick,
        death_ticks[-1] if death_ticks else 0,
    ) + int(10 * tickrate)

    rosters = _build_rosters(parser, rounds)
    slot_sample_ticks = [
        rounds[0].freeze_end_tick + 16,
        rounds[len(rounds) // 2].freeze_end_tick + 16,
        rounds[-1].freeze_end_tick + 16,
    ]
    slots = get_slots(parser, slot_sample_ticks)

    return DemoData(
        path=path,
        map_name=map_name,
        tickrate=tickrate,
        kills=kills,
        deaths=temp_deaths,
        rounds=rounds,
        player_names=player_names,
        slots=slots,
        rosters=rosters,
        match_start_tick=match_start_tick,
        last_tick=last_tick,
        warmup_rounds=warmup_rounds,
        knife_rounds=knife_rounds,
    )
