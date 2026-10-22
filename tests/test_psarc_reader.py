from __future__ import annotations

from pathlib import Path

import pytest

from rocksmith_cdlc_generator.psarc_reader import PsarcReader, PsarcReaderError

from .psarc_builder import build_psarc


def test_round_trips_named_entries(tmp_path: Path) -> None:
    path = build_psarc(
        tmp_path / "song_p.psarc",
        [
            ("manifests/songs_dlc_key/key.json", b'{"Entries": {}}', True),
            ("audio/song.wem", b"\x00" * 128, True),
            ("guitar_lead.sng", b"LEAD", True),
        ],
    )
    with PsarcReader(path) as reader:
        assert set(reader.entries) == {"manifests/songs_dlc_key/key.json", "audio/song.wem", "guitar_lead.sng"}
        assert reader.read_entry("manifests/songs_dlc_key/key.json") == b'{"Entries": {}}'
        assert reader.read_entry("guitar_lead.sng") == b"LEAD"


def test_round_trips_encrypted_toc(tmp_path: Path) -> None:
    path = build_psarc(
        tmp_path / "song_p.psarc",
        [
            ("manifests/songs_dlc_key/key.json", b'{"Entries": {}}', True),
            ("guitar_lead.sng", b"LEAD", True),
        ],
        encrypt_toc=True,
    )
    with PsarcReader(path) as reader:
        assert set(reader.entries) == {"manifests/songs_dlc_key/key.json", "guitar_lead.sng"}
        assert reader.read_entry("guitar_lead.sng") == b"LEAD"


def test_round_trips_raw_full_cluster_entry(tmp_path: Path) -> None:
    big = b"A" * 65536
    path = build_psarc(
        tmp_path / "song_p.psarc",
        [
            ("audio/song.wem", big, False),
        ],
    )
    with PsarcReader(path) as reader:
        assert reader.read_entry("audio/song.wem") == big


def test_rejects_bad_magic(tmp_path: Path) -> None:
    path = tmp_path / "bad.psarc"
    path.write_bytes(b"NOTA" + b"\x00" * 40)
    with pytest.raises(PsarcReaderError):
        PsarcReader(path)


def test_rejects_truncated_file(tmp_path: Path) -> None:
    path = tmp_path / "trunc.psarc"
    path.write_bytes(b"PSAR")
    with pytest.raises(PsarcReaderError):
        PsarcReader(path)


def test_missing_entry_raises_key_error(tmp_path: Path) -> None:
    path = build_psarc(tmp_path / "song_p.psarc", [("guitar_lead.sng", b"LEAD", True)])
    with PsarcReader(path) as reader:
        with pytest.raises(KeyError):
            reader.read_entry("nope.sng")
