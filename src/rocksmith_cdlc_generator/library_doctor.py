"""Library doctor: scan a folder of Rocksmith ``.psarc`` files and surface health issues.

Read-only by design. The scanner never modifies the source archives or the live
Rocksmith installation. An optional ``quarantine`` directory (kept outside the
scanned folder by convention) can receive *copies* of broken files for review.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .psarc_reader import EncryptedTocError, PsarcReader, PsarcReaderError

# RS2014 Remastered crashes above this many arrangements in a single song.
MAX_ARRANGEMENTS = 5

# The correct CDLC-enabler D3DX9_42.dll is ~129 KB; the stock DirectX one is ~1.8 MB.
ENABLER_DLL_MAX_BYTES = 300_000


class Issue(BaseModel):
    code: str
    severity: str
    message: str


class SongDiagnostic(BaseModel):
    path: str
    platform: str
    status: str
    issues: list[Issue] = Field(default_factory=list)
    app_id: str | None = None
    dlc_key: str | None = None
    song_key: str | None = None
    persistent_id: str | None = None
    artist: str | None = None
    title: str | None = None
    song_volume: float | None = None
    arrangement_count: int = 0
    arrangement_names: list[str] = Field(default_factory=list)
    lead_sng: int = 0
    rhythm_sng: int = 0
    bass_sng: int = 0
    audio_wem: int = 0
    sound_banks: int = 0
    xblocks: int = 0
    album_art: int = 0
    manifests: int = 0


class DuplicateGroup(BaseModel):
    field: str
    value: str
    paths: list[str]


class EnablerDllCheck(BaseModel):
    present: bool
    path: str | None = None
    size_bytes: int | None = None
    ok: bool
    message: str


class LibraryReport(BaseModel):
    schema_version: int = 1
    scanned_count: int = 0
    ok_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    songs: list[SongDiagnostic] = Field(default_factory=list)
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    enabler: EnablerDllCheck | None = None


def _detect_platform(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith("_p.psarc"):
        return "pc"
    if lower.endswith("_m.psarc"):
        return "mac"
    if lower.endswith("_xbox"):
        return "xbox"
    if lower.endswith("_ps3.psarc.edat"):
        return "ps3"
    return "unknown"


def _is_instrumental_sng(name: str, label: str) -> bool:
    base = Path(name).stem.lower()
    return name.lower().endswith(".sng") and f"_{label}" in base and "vocals" not in base


def _classify_entries(entries: tuple[str, ...]) -> dict[str, Any]:
    lead = [e for e in entries if _is_instrumental_sng(e, "lead")]
    rhythm = [e for e in entries if _is_instrumental_sng(e, "rhythm")]
    bass = [e for e in entries if _is_instrumental_sng(e, "bass")]
    wem = [e for e in entries if e.lower().endswith(".wem")]
    bnk = [e for e in entries if e.lower().endswith(".bnk")]
    xblock = [e for e in entries if e.lower().endswith(".xblock")]
    art = [e for e in entries if e.lower().endswith(".dds") and "album" in e.lower()]
    manifests = [e for e in entries if e.lower().endswith(".json") and "manifest" in e.lower()]
    return {
        "lead": lead,
        "rhythm": rhythm,
        "bass": bass,
        "wem": wem,
        "bnk": bnk,
        "xblock": xblock,
        "art": art,
        "manifests": manifests,
    }


def _extract_manifest_attrs(obj: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if isinstance(obj, dict):
        song_attrs = obj.get("Attributes") or {}
        if isinstance(song_attrs, dict):
            attrs = dict(song_attrs)
    return attrs


def _extract_arrangement_names(obj: Any) -> list[str]:
    names: list[str] = []
    if not isinstance(obj, dict):
        return names
    entries = obj.get("Entries")
    values: list[Any] = []
    if isinstance(entries, dict):
        values = list(entries.values())
    elif isinstance(entries, list):
        values = entries
    for entry in values:
        if isinstance(entry, dict):
            attr = entry.get("Attributes") or {}
            if isinstance(attr, dict):
                for key in ("ArrangementName", "ArrangementType"):
                    value = attr.get(key)
                    if isinstance(value, str) and value:
                        names.append(value)
                        break
    return names


def _read_manifest_attrs(reader: PsarcReader, manifests: list[str]) -> tuple[dict[str, Any], list[str]]:
    song_attrs: dict[str, Any] = {}
    arrangement_names: list[str] = []
    for path in manifests:
        try:
            obj = json.loads(reader.read_entry_text(path))
        except (KeyError, PsarcReaderError, json.JSONDecodeError):
            continue
        if not song_attrs:
            song_attrs = _extract_manifest_attrs(obj)
        arrangement_names.extend(_extract_arrangement_names(obj))
    return song_attrs, arrangement_names


def _diagnose_file(path: Path, expected_platform: str | None) -> SongDiagnostic:
    diag = SongDiagnostic(path=str(path), platform=_detect_platform(path.name), status="ok")
    issues: list[Issue] = []

    if expected_platform and diag.platform != expected_platform:
        if diag.platform == "unknown":
            issues.append(Issue(code="platform-unknown", severity="warning", message="unrecognized platform suffix"))
        else:
            issues.append(
                Issue(
                    code="platform-mismatch",
                    severity="error",
                    message=f"expected {expected_platform} but file targets {diag.platform}",
                )
            )

    try:
        reader = PsarcReader(path)
    except EncryptedTocError as exc:
        diag.status = "error"
        diag.issues = [Issue(code="encrypted-toc", severity="error", message=str(exc))]
        return diag
    except PsarcReaderError as exc:
        diag.status = "error"
        diag.issues = [Issue(code="corrupt", severity="error", message=str(exc))]
        return diag

    entries = reader.entries
    cls = _classify_entries(entries)
    diag.lead_sng = len(cls["lead"])
    diag.rhythm_sng = len(cls["rhythm"])
    diag.bass_sng = len(cls["bass"])
    diag.audio_wem = len(cls["wem"])
    diag.sound_banks = len(cls["bnk"])
    diag.xblocks = len(cls["xblock"])
    diag.album_art = len(cls["art"])
    diag.manifests = len(cls["manifests"])

    song_attrs, arrangement_names = _read_manifest_attrs(reader, cls["manifests"])
    diag.app_id = song_attrs.get("AppId") or None
    diag.dlc_key = song_attrs.get("DLCKey") or None
    diag.song_key = song_attrs.get("SongKey") or None
    diag.persistent_id = song_attrs.get("PersistentID") or None
    diag.artist = song_attrs.get("ArtistName") or None
    diag.title = song_attrs.get("SongName") or None
    diag.song_volume = song_attrs.get("SongVolume")
    diag.arrangement_count = len(cls["manifests"]) or len(arrangement_names)
    diag.arrangement_names = arrangement_names

    if not cls["manifests"]:
        issues.append(Issue(code="missing-manifest", severity="error", message="no manifest JSON found"))
    if not cls["wem"]:
        issues.append(Issue(code="missing-audio", severity="error", message="no WEM audio found"))
    if not cls["bnk"]:
        issues.append(Issue(code="missing-soundbank", severity="error", message="no sound bank (.bnk) found"))
    if len(cls["xblock"]) != 1:
        issues.append(
            Issue(code="bad-xblock", severity="error", message=f"expected exactly one xblock, found {len(cls['xblock'])}")
        )
    if not cls["lead"] and not cls["rhythm"] and not cls["bass"]:
        issues.append(Issue(code="missing-arrangements", severity="error", message="no instrumental SNG arrangement found"))
    if diag.arrangement_count > MAX_ARRANGEMENTS:
        issues.append(
            Issue(
                code="too-many-arrangements",
                severity="error",
                message=f"{diag.arrangement_count} arrangements exceeds the RS2014 Remastered limit of {MAX_ARRANGEMENTS}",
            )
        )
    if not diag.app_id or diag.app_id in ("0", "00000000"):
        issues.append(Issue(code="missing-appid", severity="error", message="AppId missing or zero; song will not appear in-game"))
    if diag.song_volume is None:
        issues.append(Issue(code="missing-volume", severity="warning", message="SongVolume missing; may be too loud or quiet"))
    if not diag.dlc_key:
        issues.append(Issue(code="missing-dlckey", severity="warning", message="DLCKey missing; duplicate detection unreliable"))
    if not cls["art"]:
        issues.append(Issue(code="missing-art", severity="warning", message="no album-art DDS; CFSM can restore default art"))

    if issues:
        diag.issues = issues
        diag.status = "error" if any(i.severity == "error" for i in issues) else "warning"

    return diag


def _check_enabler_dll(rocksmith_root: Path) -> EnablerDllCheck:
    dll = rocksmith_root / "D3DX9_42.dll"
    if not dll.is_file():
        return EnablerDllCheck(
            present=False,
            ok=False,
            message="D3DX9_42.dll not found in the Rocksmith root; CDLC will not appear. Run the CDLC Enabler.",
        )
    size = dll.stat().st_size
    if size > ENABLER_DLL_MAX_BYTES:
        return EnablerDllCheck(
            present=True,
            path=str(dll),
            size_bytes=size,
            ok=False,
            message=(
                f"D3DX9_42.dll is {size} bytes (~1.8 MB stock DirectX DLL); the ~129 KB CDLC enabler is required."
            ),
        )
    return EnablerDllCheck(
        present=True,
        path=str(dll),
        size_bytes=size,
        ok=True,
        message="CDLC enabler D3DX9_42.dll looks correct (~129 KB).",
    )


def _find_duplicates(songs: list[SongDiagnostic]) -> list[DuplicateGroup]:
    groups: list[DuplicateGroup] = []
    for field in ("dlc_key", "song_key", "persistent_id"):
        seen: dict[str, list[str]] = {}
        for song in songs:
            value = getattr(song, field)
            if value:
                seen.setdefault(value.lower(), []).append(song.path)
        for value, paths in seen.items():
            if len(paths) > 1:
                groups.append(DuplicateGroup(field=field, value=value, paths=paths))
    return groups


def scan_library(
    directory: Path,
    *,
    expected_platform: str | None = None,
    rocksmith_root: Path | None = None,
) -> LibraryReport:
    directory = directory.resolve()
    if not directory.is_dir():
        raise FileNotFoundError(directory)

    files = sorted(p for p in directory.rglob("*.psarc") if p.is_file())

    report = LibraryReport()
    for path in files:
        diag = _diagnose_file(path, expected_platform)
        report.songs.append(diag)

    report.scanned_count = len(report.songs)
    report.ok_count = sum(1 for s in report.songs if s.status == "ok")
    report.error_count = sum(1 for s in report.songs if s.status == "error")
    report.warning_count = sum(1 for s in report.songs if s.status == "warning")
    report.duplicates = _find_duplicates(report.songs)

    if rocksmith_root is not None:
        report.enabler = _check_enabler_dll(rocksmith_root.resolve())

    return report


def render_report(report: LibraryReport) -> str:
    lines: list[str] = []
    lines.append(f"Scanned {report.scanned_count} PSARC(s): {report.ok_count} ok, "
                 f"{report.warning_count} warning, {report.error_count} error")
    if report.enabler is not None:
        state = "OK" if report.enabler.ok else "PROBLEM"
        lines.append(f"D3DX9_42.dll: [{state}] {report.enabler.message}")
    if report.duplicates:
        lines.append("Duplicates:")
        for group in report.duplicates:
            lines.append(f"  [{group.field}] {group.value}")
            for p in group.paths:
                lines.append(f"    - {p}")
    for song in report.songs:
        if song.status == "ok":
            continue
        marker = {"error": "ERROR", "warning": "WARN"}[song.status]
        label = f"{song.artist or '?'} - {song.title or Path(song.path).stem}"
        lines.append(f"{marker} {label}  ({song.path})")
        for issue in song.issues:
            lines.append(f"    - {issue.code}: {issue.message}")
    return "\n".join(lines)


def quarantine_broken(report: LibraryReport, destination: Path) -> list[Path]:
    """Copy files that failed with an error into a quarantine directory."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for song in report.songs:
        if song.status != "error":
            continue
        source = Path(song.path)
        if not source.is_file():
            continue
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied
