# Library doctor

`cdlc doctor` scans a folder of Rocksmith `.psarc` files and reports health
issues that explain why some custom songs fail to appear, crash, or won't
start. It is read-only: it never modifies the archives or the live Rocksmith
installation.

## Why this exists

`import-psarc` only reads a single, explicitly selected `.psarc` and delegates
all PSARC/SNG decoding to the Rocksmith2014.NET bridge (which needs .NET 10 and
a build step). That is the right tool for chart import, but it is heavy for
triaging a large, possibly-broken CDLC collection.

`doctor` uses a small, dependency-free PSARC reader (`psarc_reader.py`) that
only lists entries and inflates manifest JSON. This lets a health check run on
a plain Python 3.12 runtime with no .NET/Wwise toolchain — useful on the Windows
machine where the `dlc` folder actually lives.

`psarc_reader.py` deliberately does **not** decode SNG data, construct archives,
or write anything. PSARC construction and SNG/PSARC decoding remain the
responsibility of Rocksmith2014.NET / DLC Builder (repository design rule #7).

## Usage

```
cdlc doctor <directory> [--platform pc|mac|xbox|ps3] [--rocksmith-root PATH] [--json] [--quarantine PATH]
```

- `directory` — scanned recursively for `*.psarc`.
- `--platform` — flags archives targeting a different platform (`_p` vs `_m`).
- `--rocksmith-root` — additionally checks `D3DX9_42.dll` (the CDLC enabler;
  the correct DLL is ~129 KB, not the ~1.8 MB stock DirectX one).
- `--json` — emit the full report as JSON instead of the human summary.
- `--quarantine` — *copy* error files into a review folder (source is untouched).

Exit code is `2` when any archive reports an error (useful for scripting/CI).

## Checks performed

| Code | Severity | Meaning |
|---|---|---|
| `corrupt` | error | not a valid PSARC (bad magic/version/truncated TOC) |
| `encrypted-toc` | error | ArchiveFlags == 4; requires the .NET bridge |
| `platform-mismatch` | error | targets a different platform than expected |
| `missing-manifest` | error | no `manifests/*.json` |
| `missing-audio` | error | no `.wem` audio |
| `missing-soundbank` | error | no `.bnk` sound bank |
| `bad-xblock` | error | xblock count != 1 (song packs unsupported) |
| `missing-arrangements` | error | no lead/rhythm/bass SNG |
| `too-many-arrangements` | error | >5 arrangements (RS2014 Remastered crash) |
| `missing-appid` | error | AppId missing/zero; song won't appear in-game |
| `missing-volume` | warning | SongVolume absent; may be too loud/quiet |
| `missing-dlckey` | warning | DLCKey absent; duplicate detection unreliable |
| `missing-art` | warning | no album-art DDS |
| duplicates | error | same DLCKey/SongKey/PersistentID across files |
