"""Observe physical audio outputs and report connection changes."""
from __future__ import annotations

import threading
from gi.repository import GLib
from . import core


class DeviceWatcher:
    """Poll available audio sinks without applying or stopping any routes."""

    def __init__(self, on_change, interval_seconds=5):
        self.on_change = on_change
        self.interval = max(3, int(interval_seconds))
        self.stopping = threading.Event()
        self.thread = None
        self.previous = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.thread = threading.Thread(
            target=self._run,
            name="audio-profile-device-watcher",
            daemon=True,
        )
        self.thread.start()

    def stop(self):
        self.stopping.set()

    @staticmethod
    def _read_sinks():
        devices = {}
        for item in core.get_available_sinks() or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            # Ignore this app's own virtual routing nodes.
            if name.startswith(("multi_out_", "auto_null", "null_sink")):
                continue
            devices[name] = {
                "name": name,
                "description": item.get("description") or name,
            }
        return devices

    def _run(self):
        while not self.stopping.is_set():
            try:
                current = self._read_sinks()
                if self.previous is None:
                    # Establish the baseline; don't notify for startup devices.
                    self.previous = current
                else:
                    old = self.previous
                    added = [current[k] for k in current.keys() - old.keys()]
                    removed = [old[k] for k in old.keys() - current.keys()]
                    self.previous = current
                    if added or removed:
                        GLib.idle_add(self._deliver, added, removed)
            except Exception as exc:
                # A temporary PipeWire query failure must not crash the GUI.
                print(f"Audio Profile Manager: device scan failed: {exc}")
            self.stopping.wait(self.interval)

    def _deliver(self, added, removed):
        if not self.stopping.is_set():
            try:
                self.on_change(added, removed)
            except Exception as exc:
                print(f"Audio Profile Manager: device notification failed: {exc}")
        return GLib.SOURCE_REMOVE
