# Audio Profile Manager — v0.2 development notes

## Reliability foundations

- Profile and runtime-state JSON writes now use a temporary file followed by an atomic replacement, reducing the chance of a partially written file after interruption.
- Profile identifiers are validated before they are used to construct profile/state paths.
- Profile configuration is validated before saving and after loading, including speaker names, delay values, and 0–150% volume ranges.
- Malformed or invalid saved profiles return `None` from `load_config()` instead of passing invalid values into the UI/audio startup path.
- Added standard-library regression tests for round-trip persistence, malformed data, unsafe profile IDs, invalid values, atomic writes, and default-profile protection.

## Verification

Run from the project root:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m compileall -q multi_output tests
```

The automated tests use temporary directories and do not start audio routing or call `pactl` or systemd.

## Not included in the initial reliability pass

No changes were made to the installed production application, systemd units, KDE panels, wallpaper, theme, or live audio routing. Device-aware suggestions and diagnostics UI remain future tasks.

## Portable profile import/export (follow-up development step)

- Added a versioned portable JSON export format containing profile name, outputs, delays, per-output volumes, and master volume only.
- Imports validate JSON and profile fields before saving; existing profiles are never overwritten, because the imported slug is regenerated locally.
- Legacy profile JSON files remain importable.
- Imported profiles are saved but never automatically applied to audio routing.
- Added core tests for export/import round-trips, collision safety, legacy import, unsupported formats, malformed JSON, and invalid profile data.
