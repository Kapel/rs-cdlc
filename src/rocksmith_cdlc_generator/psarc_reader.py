"""Minimal read-only PSARC reader for diagnostic purposes.

This is a deliberately scoped, dependency-free reader used by the ``doctor``
library scanner to inspect a collection of Rocksmith ``.psarc`` files. It only
lists entries and inflates small manifest/JSON files so the doctor can surface
health issues (corrupt archives, missing audio, bad AppID, arrangement count,
duplicate keys, etc.).

It intentionally does NOT decode SNG arrangement data, construct archives, or
write anything. PSARC construction and SNG/PSARC decoding remain the
responsibility of Rocksmith2014.NET / DLC Builder (repository design rule #7);
this module exists solely so a library health check can run on a plain Python
runtime with no .NET/Wwise toolchain.

Format reference (matches Rocksmith2014.NET ``Rocksmith2014.PSARC``):

Header (32 bytes, big-endian):
  magic "PSAR" (4), version major/minor (u16,u16), compression "zlib" (4),
  ToCLength (u32), ToCEntrySize (u32, 30), ToCEntryCount (u32),
  BlockSizeAlloc (u32, 65536), ArchiveFlags (u32; 4 == encrypted TOC).

TOC entry (ToCEntrySize == 30 bytes, big-endian):
  name digest (16), zIndexBegin (u32), length (u40), offset (u40).

The first TOC entry is the manifest: a newline-separated list of entry names.
Entry data is stored as 64 KB blocks; each block is either raw (block-size-table
value 0) or zlib-compressed (detected by the block's zlib header).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

_HEADER_LENGTH = 32
_TOC_ENTRY_SIZE = 30
_DEFAULT_BLOCK_SIZE = 65536


class PsarcReaderError(ValueError):
    """Raised when a file cannot be read as a PSARC (corrupt or unsupported)."""


class EncryptedTocError(PsarcReaderError):
    """Raised for PSARCs with an encrypted TOC (ArchiveFlags == 4)."""


@dataclass(frozen=True)
class PsarcEntry:
    index: int
    z_index_begin: int
    length: int
    offset: int


def _read_u40(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 5], "big")


def _read_u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "big")


def _has_zlib_header(chunk: bytes) -> bool:
    return len(chunk) >= 2 and chunk[0] == 0x78 and chunk[1] in (0x01, 0x5E, 0x9C, 0xDA)


class PsarcReader:
    """Read-only access to a Rocksmith PSARC archive."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: list[PsarcEntry] = []
        self._names: list[str] = []
        self._block_sizes: list[int] = []
        self._block_size = _DEFAULT_BLOCK_SIZE
        self._name_to_index: dict[str, int] = {}
        self._data: bytes = b""
        self._parse()

    # -- header -------------------------------------------------------------

    def _parse(self) -> None:
        try:
            data = self.path.read_bytes()
        except OSError as exc:  # pragma: no cover - depends on platform
            raise PsarcReaderError(f"cannot read file: {exc}") from exc

        if len(data) < _HEADER_LENGTH:
            raise PsarcReaderError("file too small to be a PSARC")

        self._data = data

        magic = data[0:4]
        if magic != b"PSAR":
            raise PsarcReaderError(f"bad PSARC magic: {magic!r}")

        compression = data[8:12]
        if compression != b"zlib":
            raise PsarcReaderError(f"unsupported compression method: {compression!r}")

        version_major = struct.unpack(">H", data[4:6])[0]
        version_minor = struct.unpack(">H", data[6:8])[0]

        toc_length = struct.unpack(">I", data[12:16])[0]
        toc_entry_size = struct.unpack(">I", data[16:20])[0]
        toc_entry_count = struct.unpack(">I", data[20:24])[0]
        block_size = struct.unpack(">I", data[24:28])[0]
        archive_flags = struct.unpack(">I", data[28:32])[0]

        if block_size != _DEFAULT_BLOCK_SIZE:
            raise PsarcReaderError(f"unsupported block size {block_size}")

        if archive_flags == 4:
            raise EncryptedTocError(
                "encrypted TOC (ArchiveFlags == 4); full inspection requires the "
                "Rocksmith2014.NET bridge (scripts/bootstrap_psarc_bridge.ps1)"
            )

        if toc_entry_size != _TOC_ENTRY_SIZE:
            raise PsarcReaderError(f"unsupported TOC entry size {toc_entry_size}")

        if version_major != 1 or version_minor != 4:
            # Be lenient: many older archives still parse with the 1.4 layout.
            pass

        self._block_size = block_size
        self._parse_toc(data, toc_entry_count, toc_entry_size, toc_length)

    def _parse_toc(self, data: bytes, entry_count: int, entry_size: int, toc_length: int) -> None:
        toc_start = _HEADER_LENGTH
        toc_bytes = entry_count * entry_size
        block_table_length = toc_length - _HEADER_LENGTH - toc_bytes
        if block_table_length < 0:
            raise PsarcReaderError("invalid ToC length")

        entries: list[PsarcEntry] = []
        for i in range(entry_count):
            off = toc_start + i * entry_size
            entry = data[off : off + entry_size]
            if len(entry) < entry_size:
                raise PsarcReaderError("truncated ToC entry")
            z_index_begin = struct.unpack(">I", entry[16:20])[0]
            length = _read_u40(entry, 20)
            offset = _read_u40(entry, 25)
            entries.append(PsarcEntry(i, z_index_begin, length, offset))

        ztype = self._ztype()
        if ztype is None:
            raise PsarcReaderError(f"cannot determine block-size table width for {self._block_size}")
        block_count = block_table_length // ztype
        block_sizes: list[int] = []
        base = toc_start + toc_bytes
        for j in range(block_count):
            p = base + j * ztype
            if ztype == 2:
                block_sizes.append(struct.unpack(">H", data[p : p + 2])[0])
            elif ztype == 3:
                block_sizes.append(_read_u24(data, p))
            else:
                block_sizes.append(struct.unpack(">I", data[p : p + 4])[0])

        self._entries = entries
        self._block_sizes = block_sizes
        self._read_manifest(data)

    def _ztype(self) -> int | None:
        for width in (2, 3, 4):
            if 256**width == self._block_size:
                return width
        return None

    def _read_manifest(self, data: bytes) -> None:
        if not self._entries:
            raise PsarcReaderError("PSARC has no entries")

        manifest_raw = self._inflate(self._entries[0], data)
        text = manifest_raw.decode("utf-8", errors="replace")
        names = text.split("\n")
        while names and names[-1] == "":
            names.pop()
        self._names = names
        self._name_to_index = {name: i + 1 for i, name in enumerate(names)}

    # -- inflation ----------------------------------------------------------

    def _inflate(self, entry: PsarcEntry, data: bytes) -> bytes:
        out = bytearray()
        z_index = entry.z_index_begin
        data_len = len(data)

        while len(out) < entry.length:
            if z_index >= len(self._block_sizes):
                break
            block_size = self._block_sizes[z_index]
            if block_size == 0:
                take = min(self._block_size, entry.length - len(out))
                chunk = data[entry.offset + len(out) : entry.offset + len(out) + take]
                if len(chunk) < take:
                    break
                out += chunk
            else:
                chunk = data[entry.offset + len(out) : entry.offset + len(out) + block_size]
                if len(chunk) < block_size:
                    break
                if _has_zlib_header(chunk):
                    try:
                        out += zlib.decompress(chunk)
                    except zlib.error:
                        out += chunk
                else:
                    out += chunk
            z_index += 1

        if len(out) < entry.length:
            raise PsarcReaderError(f"truncated entry (got {len(out)} of {entry.length} bytes)")
        return bytes(out[: entry.length])

    # -- public API ---------------------------------------------------------

    @property
    def entries(self) -> tuple[str, ...]:
        """Entry names (excluding the manifest pseudo-entry)."""
        return tuple(self._names)

    def read_entry(self, name: str) -> bytes:
        if name not in self._name_to_index:
            raise KeyError(name)
        entry = self._entries[self._name_to_index[name]]
        return self._inflate(entry, self._data)

    def read_entry_text(self, name: str, encoding: str = "utf-8") -> str:
        return self.read_entry(name).decode(encoding, errors="replace")

    @staticmethod
    def entry_digest(name: str) -> bytes:
        return hashlib.md5(name.encode("ascii", errors="replace")).digest()
