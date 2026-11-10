import wave
from pathlib import Path

from conftest import make_demo, make_kill, make_round

from cs2_clip_maker.composer import _shape_text_bidi, make_info_card
from cs2_clip_maker.presets import (
    _classify_tags,
    _compute_clip_window,
    _tighten_kills,
    analyze_demos_to_manifest,
    build_hud_labels,
    parse_exclude_rounds,
)
from cs2_clip_maker.highlights.models import Moment
from cs2_clip_maker.voice import write_silence_wav


def test_tighten_kills_drops_45s_idle_gap_and_keeps_dense_3k():
    # Kill #1 at tick 2000, then rapid 3K at ticks 6000, 6120, 6240 (gap > 60s!)
    kills = [
        make_kill(2000, weapon="deagle", headshot=True, victim="v1"),
        make_kill(6000, weapon="deagle", headshot=True, victim="v2"),
        make_kill(6120, weapon="deagle", headshot=True, victim="v3"),
        make_kill(6240, weapon="deagle", headshot=True, victim="v4"),
    ]
    tight = _tighten_kills(kills, tickrate=64.0, tight_gap_sec=18.0, max_action_sec=28.0)
    assert [k.tick for k in tight] == [6000, 6120, 6240]


def test_tighten_kills_preserves_5k_ace_and_clutch_finish():
    # 5K ace across ~50s (20s gaps) must stay intact as all 5 kills
    kills = [
        make_kill(50488, weapon="ak47", headshot=True, victim="v1"),
        make_kill(51127, weapon="ak47", headshot=True, victim="v2"),
        make_kill(51292, weapon="ak47", headshot=True, victim="v3"),
        make_kill(52587, weapon="ak47", headshot=True, victim="v4"),
        make_kill(53920, weapon="ak47", headshot=True, victim="v5"),
    ]
    tight = _tighten_kills(
        kills,
        tickrate=64.0,
        tight_gap_sec=23.0,
        max_action_sec=None,
        must_keep_end=True,
        allow_all_if_ace=True,
    )
    assert len(tight) == 5
    assert tight[-1].tick == 53920


def test_parse_exclude_rounds():
    rules = parse_exclude_rounds(["Dust 2:10", "5"])
    assert rules == [("dust 2", 10), (None, 5)]


def test_warmup_and_knife_rounds_excluded(monkeypatch, tmp_path):
    rounds = [
        make_round(0, 0, 10000),      # knife round
        make_round(1, 10000, 50000),  # normal round
        make_round(2, 50000, 90000),  # warmup/dm round
    ]
    kills = [
        # Knife round kills (4 knife kills)
        make_kill(2000, round_index=0, weapon="knife_karambit", victim="v1"),
        make_kill(2050, round_index=0, weapon="knife_karambit", victim="v2"),
        make_kill(2100, round_index=0, weapon="knife_karambit", victim="v3"),
        make_kill(2150, round_index=0, weapon="knife_karambit", victim="v4"),
        # Real Deagle 3K in round 2 (index 1)
        make_kill(15000, round_index=1, attacker="777", weapon="deagle", headshot=True, one_tap=True, victim="v1"),
        make_kill(15080, round_index=1, attacker="777", weapon="deagle", headshot=True, one_tap=True, victim="v2"),
        make_kill(15150, round_index=1, attacker="777", weapon="deagle", headshot=True, one_tap=True, victim="v3"),
    ]
    demo = make_demo(kills, rounds, warmup_rounds={2}, knife_rounds={0})

    fake_dem = tmp_path / "fake.dem"
    fake_dem.write_bytes(b"HL2DEMO")

    monkeypatch.setattr("cs2_clip_maker.presets.parse_demo", lambda p: demo)
    manifest = analyze_demos_to_manifest(
        [fake_dem],
        work_dir=tmp_path / "work",
        preset="deagle",
        limit=0,
        lang="ru",
    )
    assert manifest["selected_count"] == 1
    clip = manifest["clips"][0]
    assert clip["round"] == 2
    assert clip["kills_shown"] == 3
    assert "ДИГЛА" in clip["badge"] or "DEAGLE" in clip["badge"]


def test_hud_labels_ru_and_en():
    kills = [
        make_kill(3000 + i * 64, weapon="ak47", headshot=True, one_tap=True, victim=f"v{i}")
        for i in range(5)
    ]
    badge_ru, sub_ru, accent_ru = build_hud_labels("XANTARES_", "Dust 2", 13, kills, 5, True, 64.0, lang="ru")
    assert "ЭЙС" in badge_ru
    assert accent_ru == "gold"
    assert "Раунд 13" in sub_ru

    badge_en, sub_en, accent_en = build_hud_labels("XANTARES_", "Dust 2", 13, kills, 5, True, 64.0, lang="en")
    assert "ACE" in badge_en
    assert accent_en == "gold"
    assert "R13" in sub_en


def test_write_silence_wav_creates_valid_pcm(tmp_path):
    wav_path = tmp_path / "silence.wav"
    write_silence_wav(wav_path, duration_seconds=1.5)
    assert wav_path.is_file()
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 48000
        assert abs(wf.getnframes() / wf.getframerate() - 1.5) < 0.05


def test_make_info_card_handles_cyrillic_arabic_and_unicode(tmp_path):
    shaped = _shape_text_bidi("حسن • Mirage (Раунд 35) • AK-47")
    assert "Mirage (Раунд 35)" in shaped
    assert shaped.index("Mirage") > shaped.index("\u2022")

    card_path = make_info_card(
        badge_text="1v5 ЭЙС КЛАТЧ (AK-47)",
        subtitle_text="حسن • 𝒪𝒷𝒾𝓉𝑜 • Mirage (Раунд 13)",
        out_path=tmp_path / "card.png",
        accent="gold",
    )
    assert card_path.is_file()
    assert card_path.stat().st_size > 2000


def test_aces_tag_requires_five_shown_kills():
    all_kills = [make_kill(1000 + i * 2000, weapon="ak47", victim=f"v{i}") for i in range(5)]
    m = Moment(steamid="1", name="P", side="CT", round_index=5, kills=all_kills)
    assert "aces" not in _classify_tags(m, all_kills[:2], tickrate=64.0)
    assert "aces" in _classify_tags(m, all_kills, tickrate=64.0)
