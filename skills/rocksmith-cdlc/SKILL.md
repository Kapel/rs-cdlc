---
name: rocksmith-cdlc
description: Workflow for building Rocksmith 2014 custom songs (CDLC) from a song you own — scan last.fm favourites, fetch a real tab, prepare it to CustomsForge charting standards, package with DLC Builder, and validate/test. Also use to diagnose a broken CDLC collection with `cdlc doctor`.
---

# Rocksmith 2014 CDLC pipeline

End-to-end guidance for turning a song you already own into a playable,
CustomsForge-compliant CDLC, and for diagnosing an existing CDLC library.

## Hard rules (never break these)

- You must **own** the audio; the tab must be a **real human-authored** transcription
  (CustomsForge forbids *solely-AI-generated* tabs; AI is allowed only for audio/stem work).
- Personal use only; no official DLC rips; never monetize CDLC.
- **Never modify the live Rocksmith installation or player profile.** Build/stage outside it.
- Packaging stays validation-gated. Rocksmith2014.NET / DLC Builder is the authority for
  WEM, SNG, manifests, and PSARC construction.

## Current toolchain

| Step | Tool |
|---|---|
| Chart authoring (manual, sync + QC) | EOF 1.8RC15 (`ignition4.customsforge.com/eof`) |
| Tab import | EOF reads `.gp3/.gp4/.gp5` + MIDI (NOT `.gpx`) |
| Package → DD → WEM → `.psarc` | DLC Builder / `Rocksmith2014.NET` (F#, MIT) |
| OGG→WEM | Wwise 2021 authoring (`WWISEROOT`) |
| AppID | Cherub Rock = `248750` |
| Validate/repair | CFSM 2, DLC Builder validation, `cdlc doctor` |
| In-game testing | RSMods (Direct Connect, Force ReEnumeration) |

## Favourites → CDLC workflow

1. **Scan last.fm** (`user.getTopTracks` / `user.getLovedTracks`, api_key only). Normalize
   artist/title, strip `(Live)`/`(Remastered)` qualifiers.
2. **Fetch a tab** — Songsterr `/api/songs?pattern=` (keyless) → reconstruct `.gp5`
   (`SongsterrToGuitarPro` / PyGuitarPro). Avoid Ultimate Guitar (Cloudflare + ToS).
   If no real tab exists, skip — do not substitute.
3. **Prepare** audio (own a legal copy) + tab + album art into one project folder,
   enforcing charting standards: 10 s leading silence, 2 empty measures, count-in phrase,
   correct tempo map, sections/phrases, tones, DD, fret-hand positions.
4. **Chart/sync in EOF** (the one human step), then export `PART_REAL_*.xml`.
5. **Package** via DLC Builder (DD + volume calc + Cherub Rock) → `_p.psarc`.
6. **Validate** with `cdlc doctor` and DLC Builder's issue list.
7. **Test** in-game: shows in list → skyline → each arrangement → tuner → play through →
   no crash at results.

## Diagnosing a collection

```
cdlc doctor "<Rocksmith2014>\dlc" --platform pc [--rocksmith-root "<Rocksmith2014>"] [--json] [--quarantine .\quarantine]
```

### Failure-mode cheat sheet

| Symptom | Cause | Fix |
|---|---|---|
| No CDLC appear at all | `D3DX9_42.dll` missing/wrong (~129 KB, not 1.8 MB) or wrong `dlc` path | CDLC Enabler |
| Appears, freeze at tuner | AppID not owned, 100% bug, or >5 arrangements | repoint AppID / CFSM repair |
| Silent / wrong speed / too loud | `.wem` sample rate (44.1 vs 48 kHz) or missing SongVolume | re-encode / volume calc |
| Some disappear | `_p` vs `_m` mismatch, duplicate DLCKey/PID, corrupt | `cdlc doctor` |

For anything `doctor` flags but cannot fix itself, run CFSM 2 repairs
(100% bug, max arrangements, low-bass tuning, EOF tone issue) and re-test.
