"""Library doctor: scan a folder of Rocksmith ``.psarc`` files and surface health issues.

Read-only by design. The scanner never modifies the source archives or the live
Rocksmith installation. An optional ``quarantine``/``move-to`` directory can
receive *copies* (or moved files) of broken songs for review; by convention the
destination lives outside the scanned ``dlc`` folder.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .psarc_reader import PsarcReader, PsarcReaderError

# RS2014 Remastered crashes above this many arrangements in a single song.
MAX_ARRANGEMENTS = 5

# The correct CDLC-enabler D3DX9_42.dll is ~129 KB (classic) or ~253 KB (RSMods);
# the stock DirectX one is ~1.8 MB. Anything >= ~1 MB is almost certainly the stock DLL.
ENABLER_DLL_MAX_BYTES = 1_000_000

# AppIds of the Rocksmith base games; CDLC keyed to these (instead of a DLC
# AppId like Cherub Rock) does not load in-game.
BASE_GAME_APP_IDS = {"221680", "205190"}
CHERUB_ROCK_APP_ID = "248750"


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
    if lower.endswith("_xbox") or lower.endswith("_xbox.psarc"):
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
    appid = [e for e in entries if e.lower().endswith(".appid") or e.lower().endswith("appid")]
    return {
        "lead": lead,
        "rhythm": rhythm,
        "bass": bass,
        "wem": wem,
        "bnk": bnk,
        "xblock": xblock,
        "art": art,
        "manifests": manifests,
        "appid": appid,
    }


def _extract_manifest_attrs(obj: Any) -> dict[str, Any]:
    """Song-level attributes live under Entries[<persistent-id>].Attributes."""
    if not isinstance(obj, dict):
        return {}
    entries = obj.get("Entries")
    if isinstance(entries, dict) and entries:
        first = next(iter(entries.values()))
        if isinstance(first, dict):
            attrs = first.get("Attributes")
            if isinstance(attrs, dict):
                return dict(attrs)
    # Fallback: some archives use a top-level Attributes block.
    top = obj.get("Attributes")
    if isinstance(top, dict):
        return dict(top)
    return {}


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


def _read_manifest_data(reader: PsarcReader, manifests: list[str]) -> tuple[dict[str, Any], list[str]]:
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


def _read_appid(reader: PsarcReader, appid_entries: list[str]) -> str | None:
    for path in appid_entries:
        try:
            text = reader.read_entry_text(path).strip()
        except (KeyError, PsarcReaderError):
            continue
        if text:
            return text
    return None


def _diagnose_file(path: Path, expected_platform: str | None) -> SongDiagnostic:
    diag = SongDiagnostic(path=str(path), platform=_detect_platform(path.name), status="ok")
    issues: list[Issue] = []

    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size == 0:
        diag.status = "error"
        diag.issues = [Issue(code="empty-file", severity="error", message="file is empty (0 bytes)")]
        return diag

    if expected_platform and diag.platform != expected_platform:
        if diag.platform == "unknown":
            issues.append(Issue(code="platform-unknown", severity="warning", message="unrecognized platform suffix"))
        else:
            issues.append(
                Issue(
                    code="platform-mismatch",
                    severity="warning",
                    message=f"targets {diag.platform} but the expected platform is {expected_platform}",
                )
            )

    try:
        reader = PsarcReader(path)
    except PsarcReaderError as exc:
        diag.status = "error"
        diag.issues = [Issue(code="corrupt", severity="error", message=str(exc))]
        return diag

    try:
        entries = reader.entries
    except PsarcReaderError as exc:
        diag.status = "error"
        diag.issues = [Issue(code="corrupt", severity="error", message=str(exc))]
        reader.close()
        return diag

    cls = _classify_entries(entries)
    diag.lead_sng = len(cls["lead"])
    diag.rhythm_sng = len(cls["rhythm"])
    diag.bass_sng = len(cls["bass"])
    diag.audio_wem = len(cls["wem"])
    diag.sound_banks = len(cls["bnk"])
    diag.xblocks = len(cls["xblock"])
    diag.album_art = len(cls["art"])
    diag.manifests = len(cls["manifests"])

    song_attrs, arrangement_names = _read_manifest_data(reader, cls["manifests"])
    diag.app_id = _read_appid(reader, cls["appid"]) or song_attrs.get("AppId") or None
    diag.dlc_key = song_attrs.get("DLCKey") or None
    diag.song_key = song_attrs.get("SongKey") or None
    diag.persistent_id = song_attrs.get("PersistentID") or None
    diag.artist = song_attrs.get("ArtistName") or None
    diag.title = song_attrs.get("SongName") or None
    diag.song_volume = song_attrs.get("SongVolume")
    diag.arrangement_count = len(cls["manifests"]) or len(arrangement_names)
    diag.arrangement_names = arrangement_names
    reader.close()

    pack_prefixes = {e.split("/")[1] for e in cls["manifests"] if e.startswith("manifests/") and "/" in e}
    is_pack = len(cls["xblock"]) > 1 or len(pack_prefixes) > 1

    if not entries:
        issues.append(Issue(code="empty-manifest", severity="error", message="PSARC manifest is empty"))

    if is_pack:
        issues.append(
            Issue(
                code="song-pack",
                severity="warning",
                message=f"multi-song pack ({len(pack_prefixes)} songs); single-song content checks skipped",
            )
        )
    else:
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
        elif diag.app_id in BASE_GAME_APP_IDS:
            issues.append(
                Issue(
                    code="base-game-appid",
                    severity="warning",
                    message=f"keyed to the base-game AppId {diag.app_id} instead of a DLC AppId (Cherub Rock 248750); may not appear in-game",
                )
            )
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
    if size >= ENABLER_DLL_MAX_BYTES:
        return EnablerDllCheck(
            present=True,
            path=str(dll),
            size_bytes=size,
            ok=False,
            message=f"D3DX9_42.dll is {size} bytes; that is the stock DirectX DLL (~1.8 MB), not the CDLC enabler (~129 KB classic / ~253 KB RSMods).",
        )
    return EnablerDllCheck(
        present=True,
        path=str(dll),
        size_bytes=size,
        ok=True,
        message=f"CDLC enabler D3DX9_42.dll present ({size} bytes).",
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
    recursive: bool = False,
) -> LibraryReport:
    directory = directory.resolve()
    if not directory.is_dir():
        raise FileNotFoundError(directory)

    pattern = directory.rglob("*.psarc") if recursive else directory.glob("*.psarc")
    files = sorted(p for p in pattern if p.is_file())

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


def move_broken(report: LibraryReport, destination: Path) -> list[Path]:
    """Move files that failed with an error into the destination directory."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for song in report.songs:
        if song.status != "error":
            continue
        source = Path(song.path)
        if not source.is_file():
            continue
        target = destination / source.name
        if target.exists():
            target = destination / f"{source.stem}__dup{source.suffix}"
        shutil.move(str(source), str(target))
        moved.append(target)
    return moved


def fix_base_game_appid(path: Path, target: str = CHERUB_ROCK_APP_ID) -> str | None:
    """Rewrite a base-game AppId to a DLC AppId in place.

    Only rewrites when the entry is stored uncompressed and the new value is the
    same byte length as the old one (so the archive structure is unchanged).
    Returns the previous AppId if fixed, else None.
    """
    path = Path(path)
    with PsarcReader(path) as reader:
        appid_entries = [e for e in reader.entries if e.lower().endswith(".appid")]
        if not appid_entries:
            return None
        name = appid_entries[0]
        current = reader.read_entry_text(name).strip()
        if current not in BASE_GAME_APP_IDS:
            return None
        target_bytes = target.encode("ascii")
        if len(target_bytes) != len(current):
            return None
        offset = reader.entry_offset(name)

    with open(path, "r+b") as fh:
        fh.seek(offset)
        raw = fh.read(len(current))
        if raw.decode("ascii", errors="replace") != current:
            return None  # entry is compressed or already changed
        fh.seek(offset)
        fh.write(target_bytes)
    return current
