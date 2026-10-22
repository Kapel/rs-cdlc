"""Test helper: build Rocksmith-format PSARC archives (big-endian, zlib blocks).

Mirrors the on-disk layout produced by Rocksmith2014.NET ``Rocksmith2014.PSARC``
so the read-only ``psarc_reader`` can be exercised without shipping real
(redistributable-restricted) archives. Supports the encrypted-TOC variant used
by real Rocksmith archives (ArchiveFlags == 4).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from rocksmith_cdlc_generator.psarc_crypto import cfb128_encrypt

_BLOCK_SIZE = 65536
_TOC_ENTRY_SIZE = 30


def _u40(value: int) -> bytes:
    return value.to_bytes(5, "big")


def _md5(name: str) -> bytes:
    return hashlib.md5(name.encode("ascii", errors="replace")).digest()


def _block_splits(data: bytes, compress: bool) -> list[tuple[bytes, int]]:
    """Return a list of (block bytes, block-size-table value) pairs."""
    if compress:
        packed = zlib.compress(data)
        return [(packed, len(packed))]
    # Store raw: full clusters use table value 0, the final partial cluster its size.
    out: list[tuple[bytes, int]] = []
    for i in range(0, len(data), _BLOCK_SIZE):
        chunk = data[i : i + _BLOCK_SIZE]
        table_value = 0 if len(chunk) == _BLOCK_SIZE else len(chunk)
        out.append((chunk, table_value))
    if not data:
        out.append((b"", 0))
    return out


def build_psarc(
    path: Path,
    entries: list[tuple[str, bytes, bool]],
    *,
    encrypt_toc: bool = False,
) -> Path:
    """Write a PSARC to ``path``. ``entries`` are (name, data, compress) tuples."""
    names = [name for name, _, _ in entries]
    manifest = "\n".join(names)

    ordered = [("", manifest.encode("utf-8"), True)] + entries

    block_table: list[int] = []
    block_data: list[bytes] = []
    meta: list[tuple[int, int, int]] = []  # (zIndexBegin, length, data-section offset)

    data_offset = 0
    for _, data, compress in ordered:
        z_begin = len(block_table)
        meta.append((z_begin, len(data), data_offset))
        for chunk, table_value in _block_splits(data, compress):
            block_table.append(table_value)
            block_data.append(chunk)
            data_offset += len(chunk)

    block_table_bytes = b"".join(struct.pack(">H", v) for v in block_table)

    toc_body = bytearray()
    for name, (z_begin, length, _section_offset) in zip(["", *names], meta):
        toc_body += _md5(name)
        toc_body += struct.pack(">I", z_begin)
        toc_body += _u40(length)
        toc_body += _u40(0)  # placeholder offset, patched below
    toc_body += block_table_bytes

    # Patch offsets after computing ToCLength (offsets are absolute file offsets).
    toc_length = 32 + len(toc_body)
    for i, (_, _, section_offset) in enumerate(meta):
        absolute = toc_length + section_offset
        entry_start = i * _TOC_ENTRY_SIZE
        toc_body[entry_start + 25 : entry_start + 30] = _u40(absolute)

    toc_on_disk = cfb128_encrypt(bytes(toc_body)) if encrypt_toc else bytes(toc_body)

    header = bytearray()
    header += b"PSAR"
    header += struct.pack(">H", 1)
    header += struct.pack(">H", 4)
    header += b"zlib"
    header += struct.pack(">I", toc_length)
    header += struct.pack(">I", _TOC_ENTRY_SIZE)
    header += struct.pack(">I", len(ordered))
    header += struct.pack(">I", _BLOCK_SIZE)
    header += struct.pack(">I", 4 if encrypt_toc else 0)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(header)
        fh.write(toc_on_disk)
        for block in block_data:
            fh.write(block)
    return path


def manifest_json(artist: str = "Artist", title: str = "Song",
                  dlc_key: str | None = "Artist_Song", song_key: str | None = "Song",
                  song_volume: float | None = -12.0,
                  persistent_id: str = "PERSIST") -> str:
    """Build a manifest JSON matching the real Rocksmith layout.

    Song attributes live under ``Entries[<persistent-id>].Attributes`` (the AppId
    is NOT stored here; it lives in a separate ``appid.appid`` entry)."""
    import json as _json

    attrs: dict = {
        "ArtistName": artist,
        "SongName": title,
        "PersistentID": persistent_id,
        "ArrangementName": "Lead",
    }
    if dlc_key is not None:
        attrs["DLCKey"] = dlc_key
    if song_key is not None:
        attrs["SongKey"] = song_key
    if song_volume is not None:
        attrs["SongVolume"] = song_volume
    return _json.dumps({
        "Entries": {persistent_id: {"Attributes": attrs}},
        "ModelName": "DLC",
        "IterationVersion": 1,
        "InsertRoot": "Static.Session.MusicGame",
    })
