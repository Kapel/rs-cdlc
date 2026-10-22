from __future__ import annotations

from pathlib import Path

from rocksmith_cdlc_generator.library_doctor import (
    quarantine_broken,
    render_report,
    scan_library,
)

from .psarc_builder import build_psarc, manifest_json


def _healthy_entries(dlc_key: str = "Artist_Song", song_key: str = "Song", app_id: str = "248750") -> list[tuple[str, bytes, bool]]:
    manifest = manifest_json(app_id=app_id, dlc_key=dlc_key, song_key=song_key, song_volume=-12.0)
    return [
        (f"manifests/songs_dlc_{dlc_key}/{dlc_key}.json", manifest.encode("utf-8"), True),
        ("guitar_lead.sng", b"LEAD", True),
        ("guitar_rhythm.sng", b"RHYTHM", True),
        ("guitar_bass.sng", b"BASS", True),
        ("audio/song.wem", b"\x00" * 64, True),
        ("audio/song.bnk", b"\x00" * 64, True),
        ("song.xblock", b"\x00" * 8, True),
        ("album_Artist_256.dds", b"\x00" * 16, True),
    ]


def test_healthy_song_reports_ok(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries())
    report = scan_library(tmp_path)
    assert report.scanned_count == 1
    assert report.ok_count == 1
    assert report.error_count == 0
    assert report.songs[0].status == "ok"
    assert report.songs[0].app_id == "248750"
    assert report.songs[0].arrangement_count == 1


def test_missing_appid_is_error(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries(app_id=None))
    report = scan_library(tmp_path)
    song = report.songs[0]
    assert song.status == "error"
    codes = {i.code for i in song.issues}
    assert "missing-appid" in codes


def test_missing_audio_is_error(tmp_path: Path) -> None:
    entries = [e for e in _healthy_entries() if not e[0].endswith(".wem")]
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", entries)
    report = scan_library(tmp_path)
    codes = {i.code for i in report.songs[0].issues}
    assert "missing-audio" in codes


def test_corrupt_file_is_error(tmp_path: Path) -> None:
    (tmp_path / "broken_p.psarc").write_bytes(b"PSAR" + b"\x00" * 64)
    report = scan_library(tmp_path)
    assert report.error_count == 1
    assert report.songs[0].issues[0].code == "corrupt"


def test_platform_mismatch_is_error(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_m.psarc", _healthy_entries())
    report = scan_library(tmp_path, expected_platform="pc")
    codes = {i.code for i in report.songs[0].issues}
    assert "platform-mismatch" in codes


def test_duplicate_dlc_key_detected(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries())
    build_psarc(tmp_path / "Artist_Song_v2_p.psarc", _healthy_entries())
    report = scan_library(tmp_path)
    assert report.duplicates
    assert report.duplicates[0].field == "dlc_key"


def test_enabler_dll_check(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries())
    root = tmp_path / "rocksmith"
    root.mkdir()
    (root / "D3DX9_42.dll").write_bytes(b"\x00" * 120_000)
    report = scan_library(tmp_path, rocksmith_root=root)
    assert report.enabler is not None
    assert report.enabler.ok is True


def test_wrong_enabler_dll_flag(tmp_path: Path) -> None:
    root = tmp_path / "rocksmith"
    root.mkdir()
    (root / "D3DX9_42.dll").write_bytes(b"\x00" * 1_900_000)
    report = scan_library(tmp_path, rocksmith_root=root)
    assert report.enabler is not None
    assert report.enabler.ok is False


def test_render_report_mentions_errors(tmp_path: Path) -> None:
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries(app_id=None))
    report = scan_library(tmp_path)
    text = render_report(report)
    assert "missing-appid" in text


def test_quarantine_copies_error_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken_p.psarc"
    broken.write_bytes(b"PSAR" + b"\x00" * 64)
    build_psarc(tmp_path / "Artist_Song_v1_p.psarc", _healthy_entries())
    report = scan_library(tmp_path)
    destination = tmp_path / "quarantine"
    copied = quarantine_broken(report, destination)
    assert len(copied) == 1
    assert (destination / "broken_p.psarc").is_file()
    assert broken.is_file()

