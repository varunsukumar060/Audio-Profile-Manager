"""Regression tests for profile persistence; these never call pactl/systemd."""

import json
import tempfile
import unittest
from pathlib import Path

from multi_output import core


class ProfilePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apm-tests-")
        root = Path(self.temp.name)
        self.original_profiles_dir = core.PROFILES_DIR
        self.original_state_dir = core.STATE_DIR
        core.PROFILES_DIR = root / "profiles"
        core.STATE_DIR = root / "state"

    def tearDown(self):
        core.PROFILES_DIR = self.original_profiles_dir
        core.STATE_DIR = self.original_state_dir
        self.temp.cleanup()

    def test_save_and_load_profile_round_trip(self):
        config = core.MultiOutputConfig(
            slug="theatre_mode",
            name="Theatre Mode",
            speakers=[core.SpeakerConfig(
                sink_name="alsa_output.test",
                delay_ms=125,
                label="Test Speaker",
                volume_percent=83,
            )],
            master_volume_percent=91,
        )
        core.save_config(config)
        loaded = core.load_config("theatre_mode")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "Theatre Mode")
        self.assertEqual(loaded.speakers[0].sink_name, "alsa_output.test")
        self.assertEqual(loaded.speakers[0].volume_percent, 83)
        self.assertEqual(loaded.master_volume_percent, 91)

    def test_invalid_slug_cannot_escape_profile_directory(self):
        for slug in ("../outside", "/tmp/evil", "UPPER", "", "a-b"):
            with self.subTest(slug=slug):
                with self.assertRaises(ValueError):
                    core._config_path(slug)
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])

    def test_out_of_range_or_nonfinite_volumes_are_rejected(self):
        for volume in (-1, 151, float("nan"), float("inf")):
            with self.subTest(volume=volume):
                config = core.MultiOutputConfig(slug="default", master_volume_percent=volume)
                with self.assertRaises(ValueError):
                    core.save_config(config)
        self.assertFalse(core.PROFILES_DIR.exists())

    def test_invalid_speaker_values_are_rejected(self):
        config = core.MultiOutputConfig(
            slug="default",
            speakers=[core.SpeakerConfig(sink_name="", delay_ms=0)],
        )
        with self.assertRaises(ValueError):
            core.save_config(config)

        config.speakers[0].sink_name = "alsa_output.test"
        config.speakers[0].delay_ms = -0.5
        with self.assertRaises(ValueError):
            core.save_config(config)

    def test_invalid_json_profile_load_returns_none(self):
        core.PROFILES_DIR.mkdir(parents=True)
        (core.PROFILES_DIR / "default.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(core.load_config("default"))

    def test_invalid_saved_profile_values_load_as_none(self):
        core.PROFILES_DIR.mkdir(parents=True)
        (core.PROFILES_DIR / "default.json").write_text(json.dumps({
            "slug": "default",
            "name": "Broken",
            "speakers": [],
            "master_volume_percent": 999,
        }), encoding="utf-8")
        self.assertIsNone(core.load_config("default"))

    def test_atomic_write_leaves_no_temporary_file(self):
        core.save_config(core.MultiOutputConfig(slug="default", name="Default"))
        self.assertTrue((core.PROFILES_DIR / "default.json").is_file())
        self.assertEqual(list(core.PROFILES_DIR.glob("*.tmp")), [])
        self.assertEqual(list(core.PROFILES_DIR.glob(".*.tmp")), [])

    def test_default_profile_cannot_be_deleted(self):
        core.save_config(core.MultiOutputConfig(slug="default", name="Default"))
        with self.assertRaises(ValueError):
            core.delete_profile("default")
        self.assertTrue((core.PROFILES_DIR / "default.json").exists())

    def test_export_is_portable_and_excludes_runtime_state(self):
        config = core.MultiOutputConfig(
            slug="theatre_mode",
            name="Theatre Mode",
            speakers=[core.SpeakerConfig(
                sink_name="alsa_output.test", delay_ms=125,
                label="Test Speaker", volume_percent=82,
            )],
            master_volume_percent=90,
        )
        export_path = Path(self.temp.name) / "theatre-mode.json"
        core.export_profile(config, export_path)
        payload = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], core.PROFILE_EXPORT_FORMAT)
        self.assertEqual(payload["format_version"], core.PROFILE_EXPORT_VERSION)
        self.assertEqual(payload["profile"]["name"], "Theatre Mode")
        self.assertNotIn("slug", payload["profile"])
        self.assertNotIn("pids", payload)
        self.assertEqual(payload["profile"]["speakers"][0]["volume_percent"], 82)

    def test_export_import_round_trip_creates_new_profile(self):
        existing = core.MultiOutputConfig(slug="theatre_mode", name="Theatre Mode")
        core.save_config(existing)
        source = core.MultiOutputConfig(
            slug="theatre_mode",
            name="Theatre Mode",
            speakers=[core.SpeakerConfig(
                sink_name="alsa_output.test", delay_ms=35,
                label="Bedroom", volume_percent=74,
            )],
            master_volume_percent=88,
        )
        export_path = Path(self.temp.name) / "portable.json"
        core.export_profile(source, export_path)
        imported = core.import_profile(export_path)
        self.assertEqual(imported.slug, "theatre_mode_2")
        self.assertEqual(imported.name, "Theatre Mode")
        self.assertEqual(imported.speakers[0].label, "Bedroom")
        self.assertEqual(imported.speakers[0].volume_percent, 74)
        self.assertEqual(imported.master_volume_percent, 88)
        self.assertEqual(core.load_config("theatre_mode").speakers, [])

    def test_import_accepts_legacy_profile_json_and_ignores_imported_slug(self):
        core.save_config(core.MultiOutputConfig(slug="default", name="Default"))
        legacy = {
            "slug": "default",
            "name": "Imported Theatre",
            "speakers": [{"sink_name": "bluez_output.test", "delay_ms": 10, "label": "Headphones"}],
            "master_volume_percent": 100,
        }
        source = Path(self.temp.name) / "legacy.json"
        source.write_text(json.dumps(legacy), encoding="utf-8")
        imported = core.import_profile(source)
        self.assertEqual(imported.slug, "imported_theatre")
        self.assertTrue((core.PROFILES_DIR / "default.json").exists())
        self.assertEqual(imported.speakers[0].volume_percent, 100.0)

    def test_invalid_or_unsupported_import_does_not_create_profile(self):
        source = Path(self.temp.name) / "unsupported.json"
        source.write_text(json.dumps({
            "format": "someone-elses-format",
            "format_version": 1,
            "profile": {"name": "Should Not Exist", "speakers": []},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not an Audio Profile Manager"):
            core.import_profile(source)
        self.assertEqual(core.list_profiles(), [])

    def test_malformed_json_import_has_clear_error_and_no_side_effect(self):
        source = Path(self.temp.name) / "broken.json"
        source.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            core.import_profile(source)
        self.assertEqual(core.list_profiles(), [])

    def test_invalid_import_profile_is_not_saved(self):
        source = Path(self.temp.name) / "bad-profile.json"
        source.write_text(json.dumps({
            "name": "Bad Profile",
            "speakers": [{"sink_name": "", "delay_ms": 0}],
            "master_volume_percent": 100,
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            core.import_profile(source)
        self.assertEqual(core.list_profiles(), [])


    def test_full_backup_restore_round_trip(self):
        core.save_config(core.MultiOutputConfig(
            slug="living_room",
            name="Living Room",
            speakers=[core.SpeakerConfig(
                sink_name="alsa_output.test",
                delay_ms=25,
                label="Living speakers",
                volume_percent=80,
            )],
            master_volume_percent=92,
        ))
        core.save_config(core.MultiOutputConfig(
            slug="default",
            name="Default",
        ))

        backup = Path(self.temp.name) / "full-backup.json"
        core.export_profiles_backup(backup)

        payload = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], core.PROFILE_BACKUP_FORMAT)
        self.assertEqual(payload["format_version"], core.PROFILE_BACKUP_VERSION)
        self.assertEqual(payload["manifest"]["profile_count"], 2)
        self.assertNotIn("pids", payload)

        core.PROFILES_DIR = Path(self.temp.name) / "restored-profiles"
        restored, recovery = core.restore_profiles_backup(backup)

        self.assertEqual({item.slug for item in restored}, {"default", "living_room"})
        self.assertIsNone(recovery)
        loaded = core.load_config("living_room")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.speakers[0].label, "Living speakers")
        self.assertEqual(loaded.master_volume_percent, 92)

    def test_restore_preserves_existing_profiles_and_creates_recovery_backup(self):
        core.save_config(core.MultiOutputConfig(
            slug="default",
            name="My Existing Default",
        ))
        source_profiles = core.PROFILES_DIR
        backup = Path(self.temp.name) / "incoming.json"

        # Build the incoming backup in a separate temporary profile directory.
        core.PROFILES_DIR = Path(self.temp.name) / "source-profiles"
        core.save_config(core.MultiOutputConfig(
            slug="default",
            name="Backed-up Default",
        ))
        core.save_config(core.MultiOutputConfig(
            slug="bedroom",
            name="Bedroom",
        ))
        core.export_profiles_backup(backup)

        core.PROFILES_DIR = source_profiles
        restored, recovery = core.restore_profiles_backup(backup)

        self.assertEqual(core.load_config("default").name, "My Existing Default")
        self.assertEqual(core.load_config("default_2").name, "Backed-up Default")
        self.assertEqual(core.load_config("bedroom").name, "Bedroom")
        self.assertIsNotNone(recovery)
        self.assertTrue(recovery.is_file())

        recovery_payload = json.loads(recovery.read_text(encoding="utf-8"))
        self.assertEqual(recovery_payload["manifest"]["profile_count"], 1)
        self.assertEqual(recovery_payload["profiles"][0]["name"], "My Existing Default")

    def test_invalid_backup_does_not_restore_any_profiles(self):
        backup = Path(self.temp.name) / "invalid-backup.json"
        backup.write_text(json.dumps({
            "format": core.PROFILE_BACKUP_FORMAT,
            "format_version": core.PROFILE_BACKUP_VERSION,
            "manifest": {"profile_count": 2},
            "profiles": [
                {
                    "slug": "valid_one",
                    "name": "Valid One",
                    "speakers": [],
                    "master_volume_percent": 100,
                },
                {
                    "slug": "invalid_two",
                    "name": "Invalid Two",
                    "speakers": [{"sink_name": "", "delay_ms": 0}],
                    "master_volume_percent": 100,
                },
            ],
        }), encoding="utf-8")

        with self.assertRaises(ValueError):
            core.restore_profiles_backup(backup)

        self.assertEqual(core.list_profiles(), [])

    def test_backup_rejects_manifest_count_mismatch(self):
        backup = Path(self.temp.name) / "bad-manifest.json"
        backup.write_text(json.dumps({
            "format": core.PROFILE_BACKUP_FORMAT,
            "format_version": core.PROFILE_BACKUP_VERSION,
            "manifest": {"profile_count": 3},
            "profiles": [],
        }), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "profile count"):
            core.restore_profiles_backup(backup)
        self.assertEqual(core.list_profiles(), [])

    def test_backup_rejects_duplicate_profile_ids(self):
        backup = Path(self.temp.name) / "duplicate-ids.json"
        profile = {
            "slug": "same_id",
            "name": "A Profile",
            "speakers": [],
            "master_volume_percent": 100,
        }
        backup.write_text(json.dumps({
            "format": core.PROFILE_BACKUP_FORMAT,
            "format_version": core.PROFILE_BACKUP_VERSION,
            "manifest": {"profile_count": 2},
            "profiles": [profile, {**profile, "name": "Another Profile"}],
        }), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "duplicate profile ID"):
            core.restore_profiles_backup(backup)
        self.assertEqual(core.list_profiles(), [])


if __name__ == "__main__":
    unittest.main()
