# Library doctor

`cdlc doctor` scans a folder of Rocksmith `.psarc` files and reports health
issues that explain why some custom songs fail to appear, crash, or won't
start. By default it is read-only; the `--fix-appid`, `--quarantine`, and
`--move-to` flags are explicit, opt-in mutations.

## Why this exists

`import-psarc` only reads a single, explicitly selected `.psarc` and delegates
all PSARC/SNG decoding to the Rocksmith2014.NET bridge (which needs .NET 10 and
a build step). That is the right tool for chart import, but it is heavy for
triaging a large, possibly-broken CDLC collection.

`doctor` uses a small, dependency-free PSARC reader (`psarc_reader.py`) that
lists entries and inflates manifest JSON. It also decrypts the AES-256-CFB table
of contents that real Rocksmith archives use (`psarc_crypto.py`). This lets a
health check run on a plain Python 3.12 runtime with no .NET/Wwise toolchain.

`psarc_reader.py` deliberately does **not** decode SNG data or construct
archives. PSARC construction and SNG/PSARC decoding remain the responsibility of
Rocksmith2014.NET / DLC Builder (repository design rule #7). The one exception
is `--fix-appid`, which patches a single raw (uncompressed) 6-byte `appid.appid`
value in place without changing the archive structure.

## Usage

```
cdlc doctor <directory> [--recursive] [--platform pc|mac|xbox|ps3] [--rocksmith-root PATH] [--json] [--quarantine PATH] [--move-to PATH] [--fix-appid]
```

- `directory` — scanned for `*.psarc` (root only unless `--recursive`).
- `--platform` — flags archives targeting a different platform (`_p` vs `_m`).
- `--rocksmith-root` — additionally checks `D3DX9_42.dll` (the CDLC enabler).
- `--json` — emit the full report as JSON instead of the human summary.
- `--quarantine` — *copy* error files into a review folder (source is untouched).
- `--move-to` — *move* error files into a folder (removes them from the dlc folder).
- `--fix-appid` — rewrite base-game AppIds (`221680`/`205190`) to Cherub Rock (`248750`) in place.

Exit code is `2` when any archive reports an error (useful for scripting/CI).

## Checks performed

| Code | Severity | Meaning |
|---|---|---|
| `corrupt` | error | not a valid PSARC (bad magic/version/truncated TOC) |
| `empty-file` | error | zero-byte file |
| `empty-manifest` | error | PSARC manifest is empty |
| `platform-mismatch` | warning | targets a different platform than expected |
| `platform-unknown` | warning | unrecognized platform suffix |
| `missing-manifest` | error | no `manifests/*.json` |
| `missing-audio` | error | no `.wem` audio |
| `missing-soundbank` | error | no `.bnk` sound bank |
| `bad-xblock` | error | xblock count != 1 (single song expected) |
| `missing-arrangements` | error | no lead/rhythm/bass SNG |
| `too-many-arrangements` | error | >5 arrangements (RS2014 Remastered crash) |
| `missing-appid` | error | AppId missing/zero; song won't appear in-game |
| `base-game-appid` | warning | keyed to the base-game AppId (221680/205190) instead of a DLC AppId |
| `song-pack` | warning | multi-song archive; single-song checks skipped |
| `missing-volume` | warning | SongVolume absent; may be too loud/quiet |
| `missing-dlckey` | warning | DLCKey absent; duplicate detection unreliable |
| `missing-art` | warning | no album-art DDS |
| duplicates | — | same DLCKey/SongKey/PersistentID across files |

## Notes

- `encrypted-toc` was removed: the reader now decrypts AES-256-CFB TOCs itself.
- The AppId is read from the `appid.appid` entry, not the manifest JSON (song
  attributes live under `Entries[<persistent-id>].Attributes`).
