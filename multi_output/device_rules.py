"""Persistent per-device smart profile rules for the development build."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from . import core

FORMAT = "audio-profile-manager-device-rules"
VERSION = 1
VALID_MODES = {"suggest", "ignore", "auto"}
SLUG_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _path() -> Path:
    return core.CONFIG_DIR / "device-rules.json"


def _validate(rule: dict) -> dict:
    if not isinstance(rule, dict):
        raise ValueError("Each device rule must be an object.")

    name = rule.get("device_name")
    slug = rule.get("profile_slug", "")
    mode = rule.get("mode")

    if not isinstance(name, str) or not name or len(name) > 2048:
        raise ValueError("Device names must be non-empty strings up to 2048 characters.")
    if mode not in VALID_MODES:
        raise ValueError("Device rule mode must be suggest, ignore, or auto.")

    if mode == "ignore":
        slug = ""
    elif not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ValueError("A valid profile ID is required for suggest/auto rules.")

    return {
        "device_name": name,
        "profile_slug": slug,
        "mode": mode,
    }


def load_rules() -> list[dict]:
    path = _path()
    if not path.exists():
        return []

    if path.stat().st_size > 1_000_000:
        raise ValueError("The device rules file is unexpectedly large.")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The device rules file must contain a JSON object.")
    if payload.get("format") != FORMAT or payload.get("version") != VERSION:
        raise ValueError("Unsupported device rules file format or version.")

    rules = payload.get("rules")
    if not isinstance(rules, list) or len(rules) > 500:
        raise ValueError("The device rules list is invalid or too large.")

    validated = [_validate(rule) for rule in rules]
    names = [rule["device_name"] for rule in validated]
    if len(names) != len(set(names)):
        raise ValueError("The device rules file has duplicate device names.")

    return validated


def save_rules(rules: list[dict]) -> Path:
    if not isinstance(rules, list) or len(rules) > 500:
        raise ValueError("The device rules list is invalid or too large.")

    validated = [_validate(rule) for rule in rules]
    names = [rule["device_name"] for rule in validated]
    if len(names) != len(set(names)):
        raise ValueError("Only one rule per device may be saved.")

    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".device-rules-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(
                {"format": FORMAT, "version": VERSION, "rules": validated},
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    return path
