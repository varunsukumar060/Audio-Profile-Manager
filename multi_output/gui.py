"""GTK4/libadwaita GUI for pipewire-multi-output.

Run with: python3 -m multi_output.gui
"""

from __future__ import annotations

import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from . import core
from . import device_rules
from . import app_routing
from .device_watcher import DeviceWatcher
from .tray import TrayController


def _friendly_sink_type(sink_name: str) -> str:
    """Turn a PipeWire sink name into a short, human-readable transport label."""
    if sink_name.startswith("bluez_output."):
        return "Bluetooth"
    if sink_name.startswith("alsa_output.usb-"):
        return "USB Audio"
    if sink_name.startswith("alsa_output.pci-"):
        return "Built-in Audio"
    if sink_name.startswith("alsa_output."):
        return "ALSA Output"
    return sink_name


class SpeakerRow(Adw.ExpanderRow):
    """A row in the speaker list representing one output speaker."""

    def __init__(self, speaker: core.SpeakerConfig, index: int, app: MultiOutputApp):
        super().__init__()
        self.speaker = speaker
        self.index = index
        self.profile_slug = app.current_slug
        self.app = app
        self._delay_debounce_id: int = 0
        self._volume_debounce_id: int = 0

        self.set_title(speaker.label or speaker.sink_name)
        self.set_subtitle(_friendly_sink_type(speaker.sink_name))
        self.set_subtitle_lines(1)
        self.set_tooltip_text(speaker.sink_name)

        # Remove button
        remove_btn = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER)
        remove_btn.add_css_class("flat")
        remove_btn.set_tooltip_text("Remove speaker")
        remove_btn.connect("clicked", self._on_remove)
        self.add_suffix(remove_btn)

        # Delay slider as a child row inside the expander
        slider_row = Adw.ActionRow(title="Delay")

        self.delay_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 500, 5,
        )
        self.delay_scale.set_value(speaker.delay_ms)
        self.delay_scale.set_hexpand(True)
        self.delay_scale.set_size_request(200, -1)
        self.delay_scale.set_valign(Gtk.Align.CENTER)
        slider_row.add_suffix(self.delay_scale)

        delay_adj = Gtk.Adjustment(
            value=speaker.delay_ms, lower=0, upper=500,
            step_increment=1, page_increment=10,
        )
        self.delay_spin = Gtk.SpinButton(
            adjustment=delay_adj, digits=0,
            valign=Gtk.Align.CENTER,
        )
        self.delay_spin.set_width_chars(4)

        delay_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4, valign=Gtk.Align.CENTER,
        )
        delay_box.append(self.delay_spin)
        ms_label = Gtk.Label(label="ms")
        ms_label.add_css_class("dim-label")
        delay_box.append(ms_label)
        slider_row.add_suffix(delay_box)

        self._updating = False
        self.delay_scale.connect("value-changed", self._on_scale_changed)
        self.delay_spin.connect("value-changed", self._on_spin_changed)
        self.add_row(slider_row)

        # Route volume: adjusts only this profile's pw-loopback playback stream.
        volume_row = Adw.ActionRow(
            title="Output volume",
            subtitle="This output only (0–150%)",
        )
        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 150, 1,
        )
        self.volume_scale.set_value(float(speaker.volume_percent))
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_size_request(200, -1)
        self.volume_scale.set_digits(0)
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_valign(Gtk.Align.CENTER)
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        volume_row.add_suffix(self.volume_scale)
        self.add_row(volume_row)

    def _on_scale_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._updating = True
        value = scale.get_value()
        self.speaker.delay_ms = value
        self.delay_spin.set_value(value)
        self._updating = False
        self._schedule_apply()

    def _on_spin_changed(self, spin: Gtk.SpinButton) -> None:
        if self._updating:
            return
        self._updating = True
        value = spin.get_value()
        self.speaker.delay_ms = value
        self.delay_scale.set_value(value)
        self._updating = False
        self._schedule_apply()

    def _schedule_apply(self) -> None:
        if self._delay_debounce_id:
            GLib.source_remove(self._delay_debounce_id)
        self._delay_debounce_id = GLib.timeout_add(300, self._apply_delay)

    def _apply_delay(self) -> bool:
        self._delay_debounce_id = 0
        slug = self.profile_slug
        if slug != self.app.current_slug:
            return GLib.SOURCE_REMOVE
        if core.is_running(slug):
            try:
                core.update_speaker_delay(slug, self.index, self.speaker.delay_ms)
                core.save_config(self.app.config)
            except RuntimeError:
                pass
        return GLib.SOURCE_REMOVE

    def _on_volume_changed(self, scale: Gtk.Scale) -> None:
        self.speaker.volume_percent = scale.get_value()
        if self._volume_debounce_id:
            GLib.source_remove(self._volume_debounce_id)
        self._volume_debounce_id = GLib.timeout_add(250, self._apply_volume)

    def _apply_volume(self) -> bool:
        self._volume_debounce_id = 0
        slug = self.profile_slug
        if slug != self.app.current_slug:
            return GLib.SOURCE_REMOVE
        try:
            core.save_config(self.app.config)
            if core.is_running(slug):
                core.set_speaker_volume(slug, self.index, self.speaker.volume_percent)
                self.app.status_label.set_text(
                    f"{self.speaker.label or self.speaker.sink_name}: "
                    f"{round(self.speaker.volume_percent)}%"
                )
            else:
                self.app.status_label.set_text(
                    "Output volume saved. Apply the profile to activate it."
                )
        except Exception as exc:
            self.app.status_label.set_text(f"Volume error: {exc}")
        return GLib.SOURCE_REMOVE

    def _on_remove(self, _btn: Gtk.Button) -> None:
        self.app.remove_speaker(self.index)


class MultiOutputApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.pipewire_multi_output.dev",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.current_slug = "default"
        self.config = core.MultiOutputConfig()
        self.speaker_rows: list[SpeakerRow] = []
        self._switching_profile = False

    def do_activate(self) -> None:
        # Run migration
        core.migrate_if_needed()

        # Ensure the reserved Default profile exists, without overwriting it or any custom profile.
        if core.load_config("default") is None:
            core.save_config(core.MultiOutputConfig(slug="default", name="Default"))

        # Load saved config
        profiles = core.list_profiles()
        if profiles:
            self.current_slug = profiles[0]
        saved = core.load_config(self.current_slug)
        if saved:
            self.config = saved
        else:
            self.config = core.MultiOutputConfig(slug=self.current_slug)

        # Window
        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_title("Audio Profile Manager — Development")
        self.win.set_default_size(500, 650)

        # Header bar
        header = Adw.HeaderBar()

        # Save button
        save_btn = Gtk.Button(icon_name="document-save-symbolic")
        save_btn.set_tooltip_text("Save configuration")
        save_btn.connect("clicked", self._on_save)
        header.pack_start(save_btn)

        # Routing actions live in the main content so the header stays uncluttered.
        # Test tone button
        self.test_btn = Gtk.ToggleButton(icon_name="audio-speakers-symbolic")
        self.test_btn.set_tooltip_text("Play test tone for delay tuning")
        self.test_btn.connect("toggled", self._on_test_toggled)
        self._test_proc: subprocess.Popen | None = None
        self._test_ping_data: bytes | None = None
        self._test_thread: threading.Thread | None = None
        header.pack_end(self.test_btn)

        # Main content
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.append(header)

        clamp = Adw.Clamp(maximum_size=600)
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=12,
            margin_end=12,
            margin_top=12,
            margin_bottom=12,
            spacing=18,
        )
        clamp.set_child(content)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_child(clamp)
        main_box.append(scrolled)

        # Profile selector group
        profile_group = Adw.PreferencesGroup(title="Profile")

        # Profile selector combo row
        self.profile_model = Gtk.StringList()
        self._refresh_profile_model()

        self.profile_combo = Adw.ComboRow(title="Selected Profile")
        self.profile_combo.set_model(self.profile_model)
        self._select_current_profile_in_combo()
        self.profile_combo.connect("notify::selected", self._on_profile_changed)
        profile_group.add(self.profile_combo)

        # Quick setup plus profile management buttons
        profile_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
        )
        quick_setup_btn = Gtk.Button(label="Quick Setup")
        quick_setup_btn.add_css_class("flat")
        quick_setup_btn.set_tooltip_text(
            "Create a laptop-only, Bluetooth-only, multi-output, or custom profile"
        )
        quick_setup_btn.connect("clicked", self._on_quick_setup)
        profile_btn_box.append(quick_setup_btn)

        add_profile_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_profile_btn.add_css_class("flat")
        add_profile_btn.set_tooltip_text("New profile")
        add_profile_btn.connect("clicked", self._on_new_profile)
        profile_btn_box.append(add_profile_btn)

        del_profile_btn = Gtk.Button(icon_name="list-remove-symbolic")
        del_profile_btn.add_css_class("flat")
        del_profile_btn.set_tooltip_text("Custom profiles can be deleted; Default is protected")
        del_profile_btn.connect("clicked", self._on_delete_profile)
        profile_btn_box.append(del_profile_btn)
        self.del_profile_btn = del_profile_btn
        self.del_profile_btn.set_sensitive(self.current_slug != "default")

        profile_group.set_header_suffix(profile_btn_box)
        content.append(profile_group)

        # Portable profile import/export. These operations only read/write JSON;
        # they never start, stop, or change audio routing.
        profile_io_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        import_profile_btn = Gtk.Button(label="Import Profile…")
        import_profile_btn.connect("clicked", self._on_import_profile)
        profile_io_box.append(import_profile_btn)

        export_profile_btn = Gtk.Button(label="Export Profile…")
        export_profile_btn.connect("clicked", self._on_export_profile)
        profile_io_box.append(export_profile_btn)
        content.append(profile_io_box)

        smart_rules_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        smart_rules_btn = Gtk.Button(label="Smart Device Rules…")
        smart_rules_btn.connect("clicked", self._show_device_rules)
        smart_rules_box.append(smart_rules_btn)
        application_routing_btn = Gtk.Button(label="Application Routing…")
        application_routing_btn.connect(
            "clicked", self._show_application_routing
        )
        smart_rules_box.append(application_routing_btn)

        content.append(smart_rules_box)

        # Full backup/restore never changes audio routing.
        backup_restore_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        backup_all_btn = Gtk.Button(label="Backup All Profiles…")
        backup_all_btn.connect("clicked", self._on_backup_all_profiles)
        backup_restore_box.append(backup_all_btn)

        restore_all_btn = Gtk.Button(label="Restore Profiles…")
        restore_all_btn.connect("clicked", self._on_restore_profiles)
        backup_restore_box.append(restore_all_btn)
        content.append(backup_restore_box)

        # Device name group
        name_group = Adw.PreferencesGroup(title="Output Device")
        self.name_entry = Adw.EntryRow(title="Name shown in sound settings")
        self.name_entry.set_text(self.config.name)
        self.name_entry.connect("changed", self._on_name_changed)
        name_group.add(self.name_entry)
        content.append(name_group)

        # Speakers group
        self.speakers_group = Adw.PreferencesGroup(title="Speakers")

        # Add speaker button
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add speaker")
        add_btn.connect("clicked", self._on_add_speaker)
        self.speakers_group.set_header_suffix(add_btn)

        content.append(self.speakers_group)

        # Placeholder when no speakers
        self.empty_label = Gtk.Label(
            label="No speakers added. Click + to add one.",
            margin_top=12,
            margin_bottom=12,
        )
        self.empty_label.add_css_class("dim-label")
        self._empty_label_added = False

        # Explicit routing actions: changing the profile selector never routes audio.
        routing_actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        self.apply_btn = Gtk.Button(label="Apply Profile")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self._on_apply_profile)
        routing_actions.append(self.apply_btn)

        self.stop_btn = Gtk.Button(label="Stop Routing")
        self.stop_btn.add_css_class("destructive-action")
        self.stop_btn.set_sensitive(False)
        self.stop_btn.connect("clicked", self._on_stop_routing)
        routing_actions.append(self.stop_btn)
        content.append(routing_actions)

        # Master volume controls the profile's virtual sink; each row above
        # controls only its own routed playback stream.
        self._updating_master_volume = True
        master_group = Adw.PreferencesGroup(title="Volume")
        master_row = Adw.ActionRow(
            title="Master volume",
            subtitle="Overall level for this profile (0–150%)",
        )
        self.master_volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 150, 1,
        )
        self.master_volume_scale.set_value(float(self.config.master_volume_percent))
        self.master_volume_scale.set_hexpand(True)
        self.master_volume_scale.set_size_request(200, -1)
        self.master_volume_scale.set_digits(0)
        self.master_volume_scale.set_draw_value(True)
        self.master_volume_scale.set_valign(Gtk.Align.CENTER)
        self.master_volume_scale.connect("value-changed", self._on_master_volume_changed)
        master_row.add_suffix(self.master_volume_scale)
        master_group.add(master_row)
        content.append(master_group)
        self._updating_master_volume = False

        # Settings group
        settings_group = Adw.PreferencesGroup(title="Settings")
        self.autostart_row = Adw.SwitchRow(
            title="Auto-start on login",
            subtitle="Enable systemd user service",
        )
        self._refresh_autostart_state()
        self.autostart_row.connect("notify::active", self._on_autostart_toggled)
        settings_group.add(self.autostart_row)
        content.append(settings_group)

        # Status bar
        self.status_label = Gtk.Label(halign=Gtk.Align.CENTER, margin_top=6)
        self.status_label.add_css_class("dim-label")
        content.append(self.status_label)

        self.win.set_content(main_box)
        self._rebuild_speaker_list()
        self._update_status()
        if not getattr(self, "_tray_initialized", False):
            self._tray_initialized = True
            try:
                self._tray = TrayController(
                    on_open=self._show_from_tray,
                    on_context_menu=self._show_tray_quick_controls,
                    on_apply_profile=self._apply_profile_from_tray,
                    on_stop=self._stop_from_tray,
                    on_quit=self._quit_from_tray,
                )
                if self._tray.available:
                    self.hold()
                    self._tray_hold_active = True
                    self.win.connect("close-request", self._on_close_to_tray)
            except Exception as exc:
                self._tray = None
                print(f"Audio Profile Manager: tray setup failed: {exc}", file=sys.stderr)

        # Register global shortcuts independently of tray availability.
        if not getattr(self, "_global_shortcuts_initialized", False):
            self._global_shortcuts_initialized = True
            try:
                from .shortcuts import GlobalShortcutManager
                self._global_shortcuts = GlobalShortcutManager(
                    on_show=self._show_from_tray,
                    on_next_profile=self._cycle_profile_from_shortcut,
                    on_stop=self._stop_from_tray,
                    on_status=self._report_global_shortcut_status,
                )
                self._global_shortcuts.start()
            except Exception as exc:
                self._global_shortcuts = None
                print(f"Audio Profile Manager: shortcut setup failed: {exc}", file=sys.stderr)

        if not getattr(self, "_device_watcher_initialized", False):
            self._device_watcher_initialized = True
            try:
                self._install_device_notification_actions()
                self._device_watcher = DeviceWatcher(
                    on_change=self._on_audio_devices_changed,
                    interval_seconds=5,
                )
                self._device_watcher.start()
                self.connect("shutdown", self._on_device_watcher_shutdown)
            except Exception as exc:
                self._device_watcher = None
                print(f"Audio Profile Manager: device watcher unavailable: {exc}")

        self.win.present()

    # --- Profile management ---

    def _refresh_profile_model(self) -> None:
        """Repopulate the profile string list from disk."""
        self.profile_model.splice(0, self.profile_model.get_n_items(), [])
        profiles = core.list_profiles()
        if not profiles:
            profiles = ["default"]
        for slug in profiles:
            self.profile_model.append(slug)

    def _select_current_profile_in_combo(self) -> None:
        """Set the combo selection to match self.current_slug."""
        if hasattr(self, "del_profile_btn"):
            self.del_profile_btn.set_sensitive(self.current_slug != "default")
        for i in range(self.profile_model.get_n_items()):
            if self.profile_model.get_string(i) == self.current_slug:
                self._switching_profile = True
                self.profile_combo.set_selected(i)
                self._switching_profile = False
                return

    def _on_profile_changed(self, combo: Adw.ComboRow, _pspec) -> None:
        if self._switching_profile:
            return
        idx = combo.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION:
            return
        new_slug = self.profile_model.get_string(idx)
        if new_slug == self.current_slug:
            return

        # Save current config before switching
        core.save_config(self.config)

        self.current_slug = new_slug
        if hasattr(self, "del_profile_btn"):
            self.del_profile_btn.set_sensitive(new_slug != "default")
        saved = core.load_config(new_slug)
        if saved:
            self.config = saved
        else:
            self.config = core.MultiOutputConfig(slug=new_slug)

        self.name_entry.set_text(self.config.name)
        self._sync_master_volume_control()
        self._rebuild_speaker_list()
        self._update_status()
        self._refresh_autostart_state()

    def _on_quick_setup(self, _btn: Gtk.Button) -> None:
        """Create a saved profile from currently detected output devices.

        Choosing a template or creating a profile never applies/reroutes audio.
        """
        available = core.get_available_sinks()
        if not available:
            dialog = Adw.AlertDialog(
                heading="No Audio Outputs Available",
                body="Connect an audio output, then open Quick Setup again.",
            )
            dialog.add_response("ok", "OK")
            dialog.present(self.win)
            return

        mode_names = ["Laptop Only", "Bluetooth Only", "Multi-output", "Custom"]
        dialog = Adw.AlertDialog(
            heading="Quick Profile Setup",
            body=(
                "Choose a starting layout, name your profile, and select the outputs. "
                "Creating a profile does not start audio routing."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create Profile")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        form = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            margin_start=12,
            margin_end=12,
            margin_top=6,
            margin_bottom=6,
        )

        name_label = Gtk.Label(label="Profile name", xalign=0)
        form.append(name_label)
        name_entry = Gtk.Entry()
        name_entry.set_text(mode_names[0])
        name_entry.set_placeholder_text("e.g. Desk Audio")
        form.append(name_entry)

        mode_label = Gtk.Label(label="Starting layout", xalign=0)
        form.append(mode_label)
        mode_combo = Gtk.DropDown.new_from_strings(mode_names)
        form.append(mode_combo)

        outputs_label = Gtk.Label(label="Available outputs", xalign=0)
        form.append(outputs_label)
        outputs_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        outputs_list.add_css_class("boxed-list")
        output_checks = []
        for sink in available:
            description = sink.get("description") or sink.get("name", "Audio output")
            check = Gtk.CheckButton(label=description)
            check.set_tooltip_text(sink.get("name", ""))
            check.sink_data = sink
            outputs_list.append(check)
            output_checks.append(check)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(150)
        scroll.set_max_content_height(230)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(outputs_list)
        form.append(scroll)

        mode_descriptions = Gtk.Label(
            label=(
                "Templates preselect matching connected devices. You can adjust the "
                "selection before creating the profile."
            ),
            xalign=0,
            wrap=True,
        )
        mode_descriptions.add_css_class("dim-label")
        form.append(mode_descriptions)
        dialog.set_extra_child(form)

        # Keep track of generated names so we don't overwrite a name the user edited.
        last_template_name = {"value": mode_names[0]}

        def select_template(*_args) -> None:
            index = mode_combo.get_selected()
            if index >= len(mode_names):
                return
            old_generated = last_template_name["value"]
            typed_name = name_entry.get_text().strip()
            if not typed_name or typed_name == old_generated:
                name_entry.set_text(mode_names[index])
            last_template_name["value"] = mode_names[index]

            if index == 3:  # Custom starts with a blank selection for deliberate setup.
                selected_names = set()
            elif index == 2:  # Multi-output selects the available outputs.
                selected_names = {item.get("name") for item in available}
            elif index == 1:  # Bluetooth Only selects the first detected Bluetooth sink.
                bluetooth = [
                    item for item in available
                    if item.get("name", "").startswith("bluez_output.")
                    or "bluetooth" in item.get("description", "").lower()
                ]
                selected_names = {bluetooth[0]["name"]} if bluetooth else set()
            else:  # Laptop Only prefers internal PCI audio or internal/speaker labels.
                laptop = [
                    item for item in available
                    if not item.get("name", "").startswith("bluez_output.")
                    and (
                        "alsa_output.pci-" in item.get("name", "")
                        or any(token in item.get("description", "").lower()
                               for token in ("built-in", "built in", "internal", "speaker"))
                    )
                ]
                selected_names = {laptop[0]["name"]} if laptop else set()

            for check in output_checks:
                check.set_active(check.sink_data.get("name") in selected_names)

        mode_combo.connect("notify::selected", select_template)
        select_template()
        dialog.connect(
            "response",
            self._on_quick_setup_response,
            name_entry,
            mode_combo,
            output_checks,
            mode_names,
        )
        dialog.present(self.win)

    def _on_quick_setup_response(
        self,
        dialog: Adw.AlertDialog,
        response: str,
        name_entry: Gtk.Entry,
        mode_combo: Gtk.DropDown,
        output_checks: list,
        mode_names: list[str],
    ) -> None:
        """Persist a new Quick Setup profile without applying it."""
        if response != "create":
            return

        name = name_entry.get_text().strip()
        selected = [check.sink_data for check in output_checks if check.get_active()]
        if not name:
            self.status_label.set_text("Enter a name for the new profile.")
            return
        if not selected:
            self.status_label.set_text(
                "Select at least one connected output before creating the profile."
            )
            return

        mode_index = mode_combo.get_selected()
        mode_name = mode_names[mode_index] if mode_index < len(mode_names) else "Custom"
        if mode_name in ("Laptop Only", "Bluetooth Only") and len(selected) != 1:
            self.status_label.set_text(
                f"{mode_name} requires exactly one selected output. Adjust the selection and try again."
            )
            return

        if mode_name == "Multi-output" and len(selected) < 2:
            self.status_label.set_text(
                "Multi-output requires at least two selected outputs. Choose two or more devices."
            )
            return

        if mode_name == "Bluetooth Only":
            chosen_name = selected[0].get("name", "")
            chosen_desc = selected[0].get("description", "").lower()
            if not (chosen_name.startswith("bluez_output.") or "bluetooth" in chosen_desc):
                self.status_label.set_text(
                    "Bluetooth Only requires a Bluetooth output. Choose a Bluetooth device or another layout."
                )
                return
        elif mode_name == "Laptop Only":
            chosen = selected[0]
            chosen_name = chosen.get("name", "")
            chosen_desc = chosen.get("description", "").lower()
            if chosen_name.startswith("bluez_output.") or not (
                "alsa_output.pci-" in chosen_name
                or any(token in chosen_desc for token in ("built-in", "built in", "internal", "speaker"))
            ):
                self.status_label.set_text(
                    "Laptop Only requires an internal/laptop speaker output. Choose a matching device or another layout."
                )
                return

        try:
            # Save the profile being edited before selecting the newly created profile.
            core.save_config(self.config)
            slug = core._unique_slug(name)
            speakers = [
                core.SpeakerConfig(
                    sink_name=item["name"],
                    delay_ms=0,
                    label=item.get("description", item["name"]),
                    volume_percent=100.0,
                )
                for item in selected
            ]
            new_config = core.MultiOutputConfig(
                slug=slug,
                name=name,
                speakers=speakers,
                master_volume_percent=100.0,
            )
            core.save_config(new_config)
        except Exception as exc:
            self.status_label.set_text(f"Could not create profile: {exc}")
            return

        self.config = new_config
        self.current_slug = slug
        self._refresh_profile_model()
        self._select_current_profile_in_combo()
        self.name_entry.set_text(self.config.name)
        self._sync_master_volume_control()
        self._rebuild_speaker_list()
        self._update_status()
        self._refresh_autostart_state()
        self.status_label.set_text(
            f"Created '{name}' with {len(speakers)} output(s). Click Apply Profile when ready."
        )

    def _on_new_profile(self, _btn: Gtk.Button) -> None:
        """Prompt for a name and create a new profile."""
        dialog = Adw.AlertDialog(
            heading="New Profile",
            body="Enter a name for the new speaker group:",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. Living Room")
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)

        dialog.connect("response", self._on_new_profile_response, entry)
        dialog.present(self.win)

    def _on_new_profile_response(self, dialog: Adw.AlertDialog, response: str, entry: Gtk.Entry) -> None:
        if response != "create":
            return
        name = entry.get_text().strip()
        if not name:
            return

        # Save current before switching
        core.save_config(self.config)

        slug = core._unique_slug(name)
        self.config = core.MultiOutputConfig(slug=slug, name=name)
        core.save_config(self.config)

        self.current_slug = slug
        self._refresh_profile_model()
        self._select_current_profile_in_combo()
        self.name_entry.set_text(self.config.name)
        self._sync_master_volume_control()
        self._rebuild_speaker_list()
        self._update_status()
        self._refresh_autostart_state()
        self.status_label.set_text(f"Created profile '{slug}'.")

    def _on_delete_profile(self, _btn: Gtk.Button) -> None:
        """Delete the current custom profile after confirmation."""
        if self.current_slug == "default":
            self.status_label.set_text("The Default profile is protected and cannot be deleted.")
            return

        profiles = core.list_profiles()
        if len(profiles) <= 1:
            self.status_label.set_text("Cannot delete the only profile.")
            return

        dialog = Adw.AlertDialog(
            heading="Delete Profile?",
            body=f"Delete profile '{self.current_slug}'? This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_delete_profile_response)
        dialog.present(self.win)

    def _on_delete_profile_response(self, dialog: Adw.AlertDialog, response: str) -> None:
        if response != "delete":
            return

        old_slug = self.current_slug
        if core.is_running(old_slug):
            core.stop(old_slug)
        core.delete_profile(old_slug)

        # Switch to another profile
        profiles = core.list_profiles()
        self.current_slug = profiles[0] if profiles else "default"
        saved = core.load_config(self.current_slug)
        if saved:
            self.config = saved
        else:
            self.config = core.MultiOutputConfig(slug=self.current_slug)

        self._refresh_profile_model()
        self._select_current_profile_in_combo()
        self.name_entry.set_text(self.config.name)
        self._sync_master_volume_control()
        self._rebuild_speaker_list()
        self._update_status()
        self._refresh_autostart_state()
        self.status_label.set_text(f"Deleted profile '{old_slug}'.")

    # --- Portable profile import/export ---

    def _on_export_profile(self, _btn: Gtk.Button) -> None:
        """Ask where to export the profile currently shown in the editor."""
        chooser = Gtk.FileChooserNative.new(
            "Export Audio Profile",
            self.win,
            Gtk.FileChooserAction.SAVE,
            "Export",
            "Cancel",
        )
        chooser.set_current_name(f"{core.slugify(self.config.name)}.json")
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON profile files")
        json_filter.add_pattern("*.json")
        chooser.add_filter(json_filter)
        chooser.connect("response", self._on_export_profile_response)
        chooser.show()

    def _on_export_profile_response(self, chooser: Gtk.FileChooserNative, response: int) -> None:
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return
            selected_file = chooser.get_file()
            path = selected_file.get_path() if selected_file else None
            if not path:
                raise ValueError("Please choose a local file path for the export.")
            exported = core.export_profile(self.config, path)
            self.status_label.set_text(f"Exported profile to {exported.name}.")
        except Exception as exc:
            self.status_label.set_text(f"Could not export profile: {exc}")
        finally:
            chooser.destroy()

    def _on_import_profile(self, _btn: Gtk.Button) -> None:
        """Open a profile JSON file and import it as a new saved profile."""
        chooser = Gtk.FileChooserNative.new(
            "Import Audio Profile",
            self.win,
            Gtk.FileChooserAction.OPEN,
            "Import",
            "Cancel",
        )
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON profile files")
        json_filter.add_pattern("*.json")
        chooser.add_filter(json_filter)
        chooser.connect("response", self._on_import_profile_response)
        chooser.show()

    def _on_import_profile_response(self, chooser: Gtk.FileChooserNative, response: int) -> None:
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return
            selected_file = chooser.get_file()
            path = selected_file.get_path() if selected_file else None
            if not path:
                raise ValueError("Please choose a local JSON profile file.")

            # Preserve the profile currently being edited before switching away.
            core.save_config(self.config)
            imported = core.import_profile(path)
        except Exception as exc:
            self.status_label.set_text(f"Could not import profile: {exc}")
            return
        finally:
            chooser.destroy()

        self.config = imported
        self.current_slug = imported.slug
        self._refresh_profile_model()
        self._select_current_profile_in_combo()
        self.name_entry.set_text(self.config.name)
        self._sync_master_volume_control()
        self._rebuild_speaker_list()
        self._update_status()
        self._refresh_autostart_state()
        self.status_label.set_text(
            f"Imported '{imported.name}' as '{imported.slug}'. Saved only; click Apply Profile when ready."
        )

    # --- Full profile backup and restore ---

    def _on_backup_all_profiles(self, _btn: Gtk.Button) -> None:
        """Choose a destination for a backup of all saved profiles."""
        chooser = Gtk.FileChooserNative.new(
            "Back Up All Audio Profiles",
            self.win,
            Gtk.FileChooserAction.SAVE,
            "Save Backup",
            "Cancel",
        )
        chooser.set_current_name("audio-profile-manager-backup.json")
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON backup files")
        json_filter.add_pattern("*.json")
        chooser.add_filter(json_filter)
        chooser.connect("response", self._on_backup_all_profiles_response)
        chooser.show()

    def _on_backup_all_profiles_response(
        self, chooser: Gtk.FileChooserNative, response: int
    ) -> None:
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return

            selected_file = chooser.get_file()
            path = selected_file.get_path() if selected_file else None
            if not path:
                raise ValueError("Please choose a local file path for the backup.")

            exported = core.export_profiles_backup(path)
            self.status_label.set_text(
                f"Backed up all saved profiles to {exported.name}."
            )
        except Exception as exc:
            self.status_label.set_text(f"Could not back up profiles: {exc}")
        finally:
            chooser.destroy()

    def _on_restore_profiles(self, _btn: Gtk.Button) -> None:
        """Choose a full backup and ask for confirmation before restoring."""
        chooser = Gtk.FileChooserNative.new(
            "Select Audio Profile Backup",
            self.win,
            Gtk.FileChooserAction.OPEN,
            "Continue",
            "Cancel",
        )
        json_filter = Gtk.FileFilter()
        json_filter.set_name("JSON backup files")
        json_filter.add_pattern("*.json")
        chooser.add_filter(json_filter)
        chooser.connect("response", self._on_restore_profiles_response)
        chooser.show()

    def _on_restore_profiles_response(
        self, chooser: Gtk.FileChooserNative, response: int
    ) -> None:
        path = None
        try:
            if response != Gtk.ResponseType.ACCEPT:
                return

            selected_file = chooser.get_file()
            path = selected_file.get_path() if selected_file else None
            if not path:
                raise ValueError("Please choose a local JSON backup file.")
        except Exception as exc:
            self.status_label.set_text(f"Could not select backup: {exc}")
            return
        finally:
            chooser.destroy()

        self._pending_profiles_restore_path = path
        dialog = Adw.AlertDialog(
            heading="Restore Saved Profiles?",
            body=(
                "Profiles will be added to your saved profiles. Existing profiles "
                "will not be overwritten; colliding IDs will receive new IDs. "
                "A recovery backup of existing profiles will be created first. "
                "Restoring does not apply profiles or change audio routing."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore Profiles")
        dialog.set_response_appearance(
            "restore", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect("response", self._on_restore_profiles_confirmed)
        dialog.present(self.win)

    def _on_restore_profiles_confirmed(
        self, _dialog: Adw.AlertDialog, response: str
    ) -> None:
        """Restore saved profiles only after the user confirms."""
        path = getattr(self, "_pending_profiles_restore_path", None)
        self._pending_profiles_restore_path = None

        if response != "restore" or not path:
            self.status_label.set_text("Profile restore cancelled.")
            return

        try:
            restored, recovery_path = core.restore_profiles_backup(path)

            # Refresh the selector without changing the current editor profile
            # or starting, stopping, or rerouting any audio.
            self._refresh_profile_model()
            self._select_current_profile_in_combo()

            message = (
                f"Restored {len(restored)} profile(s). "
                "Audio routing was not changed."
            )
            if recovery_path:
                message += f" Recovery backup: {recovery_path.name}."
            self.status_label.set_text(message)
        except Exception as exc:
            self.status_label.set_text(f"Could not restore profiles: {exc}")

    # --- Speaker list ---

    def _rebuild_speaker_list(self) -> None:
        """Rebuild the speaker list UI from current config."""
        for row in self.speaker_rows:
            self.speakers_group.remove(row)
        self.speaker_rows.clear()

        if not self.config.speakers:
            if not self._empty_label_added:
                self.speakers_group.add(self.empty_label)
                self._empty_label_added = True
        else:
            if self._empty_label_added:
                self.speakers_group.remove(self.empty_label)
                self._empty_label_added = False
            for i, speaker in enumerate(self.config.speakers):
                row = SpeakerRow(speaker, i, self)
                self.speakers_group.add(row)
                self.speaker_rows.append(row)

    def _update_status(self) -> None:
        """Update routing status without changing audio routing."""
        running = core.is_running(self.current_slug)
        active_profiles = core.list_running_profiles()
        self.stop_btn.set_sensitive(bool(active_profiles))

        if running:
            state = core.load_state(self.current_slug)
            n = len(state.speakers) if state else 0
            self.status_label.set_text(f"Profile applied to {n} output(s)")
        elif active_profiles:
            active = ", ".join(active_profiles)
            self.status_label.set_text(
                f"Another profile is routing ({active}). Apply this profile to switch."
            )
        elif self.config.speakers:
            self.status_label.set_text(
                f"Ready to apply — {len(self.config.speakers)} output(s) selected"
            )
        else:
            self.status_label.set_text("Choose outputs, then apply this profile.")

    def _refresh_autostart_state(self) -> None:
        """Keep systemd controls disabled in the development build."""
        self._toggling_autostart = True
        try:
            self.autostart_row.set_active(False)
            self.autostart_row.set_sensitive(False)
            self.autostart_row.set_subtitle(
                "Disabled in development to protect the existing service"
            )
        finally:
            self._toggling_autostart = False

    def _on_test_toggled(self, btn: Gtk.ToggleButton) -> None:
        if btn.get_active():
            self._test_ping_data = core.generate_ping(frequency=1000, duration_ms=100)
            self._test_stop = threading.Event()
            self._test_thread = threading.Thread(
                target=self._test_tone_loop, daemon=True,
            )
            self._test_thread.start()
            self.status_label.set_text("Playing test tone... toggle off to stop.")
        else:
            self._test_stop.set()
            if self._test_proc and self._test_proc.poll() is None:
                self._test_proc.terminate()
            self._test_thread = None
            self._update_status()

    def _test_tone_loop(self) -> None:
        cmd = ["paplay", "--raw", "--rate=48000", "--channels=1", "--format=s16le"]
        while not self._test_stop.is_set():
            self._test_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            self._test_proc.communicate(input=self._test_ping_data)
            self._test_stop.wait(timeout=1.5)
        self._test_proc = None

    def _on_name_changed(self, entry: Adw.EntryRow) -> None:
        self.config.name = entry.get_text()

    # --- System tray callbacks ---

    def _show_from_tray(self) -> None:
        if self.win:
            self.win.present()

    def _on_close_to_tray(self, _window: Adw.ApplicationWindow) -> bool:
        """Hide instead of quitting when the system tray is available."""
        if getattr(self, "_tray", None) and self._tray.available:
            self.win.set_visible(False)
            return True
        return False

    def _show_tray_quick_controls(self) -> None:
        """Show quick audio actions when the tray icon is right-clicked."""
        existing = getattr(self, "_tray_controls_window", None)
        if existing is not None and existing.get_visible():
            existing.present()
            return

        window = Adw.Window(application=self)
        window.set_title("Audio Profile Manager — Quick Controls")
        window.set_default_size(360, 500)
        window.set_modal(False)
        self._tray_controls_window = window

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        heading = Gtk.Label(label="Quick Audio Controls", xalign=0)
        heading.add_css_class("title-2")
        content.append(heading)

        active = core.list_running_profiles()
        if active:
            status_text = "Currently routing: " + ", ".join(active)
        else:
            status_text = "No profile is currently routing audio."

        status = Gtk.Label(label=status_text, xalign=0, wrap=True)
        status.add_css_class("dim-label")
        content.append(status)

        open_button = Gtk.Button(label="Open Main Window")
        open_button.connect(
            "clicked", self._on_tray_quick_open_clicked, window
        )
        content.append(open_button)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        content.append(separator)

        profiles_heading = Gtk.Label(label="Apply a saved profile", xalign=0)
        profiles_heading.add_css_class("heading")
        content.append(profiles_heading)

        profile_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        scroller.set_vexpand(True)
        scroller.set_child(profile_list)
        content.append(scroller)

        found_profile = False
        for slug in core.list_profiles():
            config = core.load_config(slug)
            if config is None:
                continue
            found_profile = True
            label = f"Apply: {config.name}"
            if slug in active:
                label += " (active)"
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.FILL)
            button.connect(
                "clicked",
                self._on_tray_quick_apply_clicked,
                window,
                slug,
            )
            profile_list.append(button)

        if not found_profile:
            profile_list.append(
                Gtk.Label(label="No valid saved profiles are available.", xalign=0)
            )

        actions = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        stop_button = Gtk.Button(label="Stop Routing")
        stop_button.set_hexpand(True)
        stop_button.set_sensitive(bool(active))
        stop_button.connect(
            "clicked", self._on_tray_quick_stop_clicked, window
        )
        actions.append(stop_button)

        close_button = Gtk.Button(label="Close")
        close_button.connect(
            "clicked", self._on_tray_quick_close_clicked, window
        )
        actions.append(close_button)
        content.append(actions)

        window.set_content(content)
        window.present()

    def _on_tray_quick_apply_clicked(
        self, _button: Gtk.Button, window: Adw.Window, slug: str
    ) -> None:
        window.close()
        self._tray_controls_window = None
        self._apply_profile_from_tray(slug)

    def _on_tray_quick_stop_clicked(
        self, _button: Gtk.Button, window: Adw.Window
    ) -> None:
        window.close()
        self._tray_controls_window = None
        self._stop_from_tray()

    def _on_tray_quick_open_clicked(
        self, _button: Gtk.Button, window: Adw.Window
    ) -> None:
        window.close()
        self._tray_controls_window = None
        self._show_from_tray()

    def _on_tray_quick_close_clicked(
        self, _button: Gtk.Button, window: Adw.Window
    ) -> None:
        window.close()
        self._tray_controls_window = None

    # --- Device-aware profile suggestions ---

    def _install_device_notification_actions(self) -> None:
        if getattr(self, "_device_actions_installed", False):
            return

        actions = (
            ("device-show-window", None, self._on_device_show_action),
            ("device-stop-routing", None, self._on_device_stop_action),
            (
                "apply-suggested-profile",
                GLib.VariantType.new("s"),
                self._on_apply_suggested_profile_action,
            ),
        )
        for name, parameter_type, callback in actions:
            action = Gio.SimpleAction.new(name, parameter_type)
            action.connect("activate", callback)
            self.add_action(action)

        self._device_actions_installed = True

    def _on_device_show_action(self, _action, _parameter) -> None:
        self._show_from_tray()

    def _on_device_stop_action(self, _action, _parameter) -> None:
        self._stop_from_tray()

    def _on_apply_suggested_profile_action(self, _action, parameter) -> None:
        if parameter is None:
            return
        slug = parameter.get_string()
        self._show_from_tray()
        self._apply_profile_from_tray(slug)

    def _send_device_notification(self, notification_id, title, body, buttons=()):
        try:
            notification = Gio.Notification.new(title)
            notification.set_body(body)
            for label, action_name in buttons:
                notification.add_button(label, action_name)
            self.send_notification(notification_id, notification)
        except Exception as exc:
            print(f"Audio Profile Manager: notification unavailable: {exc}")

    def _on_audio_devices_changed(self, added, removed) -> None:
        """Process device events using explicit rules and safe suggestions."""
        import hashlib

        try:
            rules = {
                rule["device_name"]: rule
                for rule in device_rules.load_rules()
            }
        except Exception as exc:
            print(f"Audio Profile Manager: could not read device rules: {exc}")
            rules = {}

        active = set(core.list_running_profiles())
        saved = []
        for slug in core.list_profiles():
            config = core.load_config(slug)
            if config is not None:
                saved.append((slug, config))

        # Handle explicit rules first. Auto mode only works because the user
        # explicitly enabled it for this device in the rules window.
        handled = set()
        for device in added:
            name = device["name"]
            rule = rules.get(name)
            if not rule:
                continue

            handled.add(name)
            mode = rule["mode"]

            if mode == "ignore":
                continue

            slug = rule.get("profile_slug", "")
            config = core.load_config(slug) if slug else None
            if config is None:
                self._send_device_notification(
                    "device-rule-invalid",
                    "Saved device rule needs attention",
                    (
                        f"The configured profile for {device.get('description') or name} "
                        "is no longer available. Open the app to edit the rule."
                    ),
                    (("Open Audio Profile Manager", "app.device-show-window"),),
                )
                continue

            if mode == "auto":
                if slug not in active:
                    self._apply_profile_from_tray(slug)
                    active = set(core.list_running_profiles())
                    self._send_device_notification(
                        f"device-auto-applied-{hashlib.sha256(name.encode()).hexdigest()[:12]}",
                        "Audio profile switched",
                        (
                            f"Because you enabled automatic switching for "
                            f"{device.get('description') or name}, the app applied "
                            f"'{config.name}'."
                        ),
                        (("Open Audio Profile Manager", "app.device-show-window"),),
                    )
            elif mode == "suggest" and slug not in active:
                self._send_device_notification(
                    f"device-connected-{hashlib.sha256(name.encode()).hexdigest()[:12]}",
                    "Audio output connected",
                    (
                        f"{device.get('description') or name} is available. "
                        f"Your configured profile is '{config.name}'. "
                        "Nothing was switched automatically."
                    ),
                    (
                        (
                            f"Apply {config.name}",
                            f"app.apply-suggested-profile::'{slug}'",
                        ),
                        ("Open Audio Profile Manager", "app.device-show-window"),
                    ),
                )

        # Preserve the original behavior for devices without a saved rule:
        # suggest a matching profile, never switch automatically.
        for device in added:
            name = device["name"]
            if name in handled or name in rules:
                continue

            matches = [
                (slug, config)
                for slug, config in saved
                if slug not in active
                and any(s.sink_name == name for s in config.speakers)
            ]
            token = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]

            if len(matches) == 1:
                slug, config = matches[0]
                self._send_device_notification(
                    f"device-connected-{token}",
                    "Audio output connected",
                    (
                        f"{device.get('description') or name} is available. "
                        f"The saved profile '{config.name}' uses this output. "
                        "Nothing was switched automatically."
                    ),
                    (
                        (
                            f"Apply {config.name}",
                            f"app.apply-suggested-profile::'{slug}'",
                        ),
                        ("Open Audio Profile Manager", "app.device-show-window"),
                    ),
                )
            elif len(matches) > 1:
                names = ", ".join(config.name for _, config in matches[:4])
                self._send_device_notification(
                    f"device-connected-{token}",
                    "Audio output connected",
                    (
                        f"{device.get('description') or name} is available. "
                        f"Matching profiles: {names}. Open the app to choose."
                    ),
                    (("Open Audio Profile Manager", "app.device-show-window"),),
                )

        # Warn on disconnects when an active profile refers to that output.
        for device in removed:
            name = device["name"]
            rule = rules.get(name)
            if rule and rule["mode"] == "ignore":
                continue

            affected = []
            for slug in core.list_running_profiles():
                config = core.load_config(slug)
                if config and any(s.sink_name == name for s in config.speakers):
                    affected.append(config.name)

            if affected:
                token = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
                self._send_device_notification(
                    f"device-disconnected-{token}",
                    "Audio output disconnected",
                    (
                        f"{device.get('description') or name} disconnected while "
                        f"active profile(s) {', '.join(affected)} use it. "
                        "Routing has not been changed."
                    ),
                    (
                        ("Open Audio Profile Manager", "app.device-show-window"),
                        ("Stop Routing", "app.device-stop-routing"),
                    ),
                )


    def _on_device_watcher_shutdown(self, *_args) -> None:
        watcher = getattr(self, "_device_watcher", None)
        if watcher is not None:
            watcher.stop()


    # --- Configurable smart device rules ---

    # --- Per-application audio routing ---

    def _show_application_routing(self, _button=None) -> None:
        """Open a read-only stream list with explicit per-stream Move actions."""
        window = getattr(self, "_application_routing_window", None)
        if window is not None:
            try:
                if window.get_visible():
                    self._refresh_application_routing(window)
                    window.present()
                    return
            except Exception:
                pass

        window = Adw.Window(application=self)
        window.set_title("Audio Profile Manager — Application Routing")
        window.set_transient_for(self.win)
        window.set_default_size(820, 560)
        self._application_routing_window = window
        self._populate_application_routing(window)
        window.present()

    def _refresh_application_routing(self, _button, window) -> None:
        self._populate_application_routing(window)

    def _populate_application_routing(self, window) -> None:
        """Rebuild the stream list without changing any routing."""
        from . import app_routing

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=16,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )

        heading = Gtk.Label(label="Application Routing", xalign=0)
        heading.add_css_class("title-2")
        root.append(heading)

        warning = Gtk.Label(
            label=(
                "Choose an output for one application stream, then click Move. "
                "Moving a stream to a physical output sends it directly there; "
                "choosing an available multi-output virtual sink routes it "
                "through that profile. Opening or refreshing this list changes "
                "nothing."
            ),
            xalign=0,
            wrap=True,
        )
        warning.add_css_class("dim-label")
        root.append(warning)

        top_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
        )
        top_bar.set_halign(Gtk.Align.END)
        refresh_button = Gtk.Button(label="Refresh")
        refresh_button.connect(
            "clicked", self._refresh_application_routing, window
        )
        top_bar.append(refresh_button)
        root.append(top_bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        scroll.set_vexpand(True)

        stream_list = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
        )
        scroll.set_child(stream_list)
        root.append(scroll)

        try:
            sinks = app_routing.list_output_sinks()
            streams = app_routing.list_sink_inputs()
        except Exception as exc:
            error_label = Gtk.Label(
                label=f"Could not read audio streams: {exc}",
                xalign=0,
                wrap=True,
            )
            error_label.add_css_class("error")
            stream_list.append(error_label)
            window.set_content(root)
            return

        if not sinks:
            stream_list.append(
                Gtk.Label(
                    label="No audio output sinks are currently available.",
                    xalign=0,
                    wrap=True,
                )
            )

        if not streams:
            stream_list.append(
                Gtk.Label(
                    label=(
                        "No application audio streams were found. "
                        "Start playing audio in an application and press Refresh."
                    ),
                    xalign=0,
                    wrap=True,
                )
            )

        sink_names = [sink["name"] for sink in sinks]
        sink_labels = [
            f'{sink["description"]} — {sink["name"]}'
            for sink in sinks
        ]

        for stream in streams:
            group = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=6,
            )

            title = Gtk.Label(
                label=stream["application"],
                xalign=0,
                wrap=True,
            )
            title.add_css_class("heading")
            group.append(title)

            subtitle = Gtk.Label(
                label=(
                    f'Current output: {stream["current_sink_description"]}'
                    + (
                        f' ({stream["current_sink_name"]})'
                        if stream["current_sink_name"] else ""
                    )
                ),
                xalign=0,
                wrap=True,
            )
            subtitle.add_css_class("dim-label")
            group.append(subtitle)

            controls = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )

            if sinks:
                dropdown = Gtk.DropDown.new_from_strings(sink_labels)
                dropdown.set_hexpand(True)

                current_name = stream["current_sink_name"]
                if current_name in sink_names:
                    dropdown.set_selected(sink_names.index(current_name))

                move_button = Gtk.Button(label="Move")
                move_button.add_css_class("suggested-action")
                move_button.connect(
                    "clicked",
                    self._move_application_stream,
                    window,
                    stream["id"],
                    dropdown,
                    sink_names,
                )
                controls.append(dropdown)
                controls.append(move_button)
            else:
                controls.append(
                    Gtk.Label(label="No destination available", xalign=0)
                )

            group.append(controls)
            stream_list.append(group)
            stream_list.append(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            )

        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda _button: window.close())
        close_button.set_halign(Gtk.Align.END)
        root.append(close_button)

        window.set_content(root)

    def _move_application_stream(
        self, _button, window, stream_id, dropdown, sink_names
    ) -> None:
        """Move only the selected stream after the user clicks Move."""
        from . import app_routing

        selected = dropdown.get_selected()
        if selected >= len(sink_names):
            self.status_label.set_text("Choose a valid audio output.")
            return

        target = sink_names[selected]
        try:
            app_routing.move_stream(stream_id, target)
            self.status_label.set_text(
                f"Moved audio stream {stream_id} to '{target}'."
            )
            self._populate_application_routing(window)
        except Exception as exc:
            self.status_label.set_text(
                f"Could not move application audio: {exc}"
            )
            # Refresh in case the stream ended or the target disappeared.
            self._populate_application_routing(window)


    def _show_device_rules(self, _button=None) -> None:
        """Open the development-only editor for device-to-profile rules."""
        try:
            devices = [
                d for d in (core.get_available_sinks() or [])
                if isinstance(d, dict)
                and isinstance(d.get("name"), str)
                and d.get("name")
                and not d["name"].startswith(("multi_out_", "auto_null", "null_sink"))
            ]
            configs = []
            for slug in core.list_profiles():
                config = core.load_config(slug)
                if config is not None:
                    configs.append((slug, config))
            rules = device_rules.load_rules()
        except Exception as exc:
            self.status_label.set_text(f"Could not load device rules: {exc}")
            return

        window = Adw.Window(application=self)
        window.set_title("Smart Device Rules")
        window.set_transient_for(self.win)
        window.set_modal(True)
        window.set_default_size(560, 570)
        self._device_rules_window = window

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        title = Gtk.Label(label="Smart Device Rules", xalign=0)
        title.add_css_class("title-2")
        root.append(title)

        description = Gtk.Label(
            label=(
                "Choose what should happen when an audio output connects. "
                "Suggestion-only mode is the default. Automatic mode can "
                "change active audio routing and requires confirmation."
            ),
            xalign=0,
            wrap=True,
        )
        description.add_css_class("dim-label")
        root.append(description)

        device_names = [d["name"] for d in devices]
        device_labels = [
            f'{d.get("description") or d["name"]} — {d["name"]}'
            for d in devices
        ]
        if not device_labels:
            device_labels = ["No audio outputs currently detected"]

        profile_slugs = [slug for slug, _config in configs]
        profile_labels = [
            f"{config.name} ({slug})" for slug, config in configs
        ]
        if not profile_labels:
            profile_labels = ["No valid saved profiles"]

        device_dropdown = Gtk.DropDown.new_from_strings(device_labels)
        profile_dropdown = Gtk.DropDown.new_from_strings(profile_labels)
        mode_dropdown = Gtk.DropDown.new_from_strings([
            "Suggest a profile (recommended)",
            "Ignore this device",
            "Automatically apply a profile",
        ])

        for label_text, widget in (
            ("Audio device", device_dropdown),
            ("Saved profile", profile_dropdown),
            ("When the device connects", mode_dropdown),
        ):
            label = Gtk.Label(label=label_text, xalign=0)
            root.append(label)
            widget.set_hexpand(True)
            root.append(widget)

        automatic_note = Gtk.Label(
            label=(
                "Automatic mode applies the selected profile when the "
                "configured device connects. You will be asked to confirm "
                "before this rule is saved."
            ),
            xalign=0,
            wrap=True,
        )
        automatic_note.add_css_class("dim-label")
        root.append(automatic_note)

        save_button = Gtk.Button(label="Save Device Rule")
        save_button.add_css_class("suggested-action")

        existing_heading = Gtk.Label(label="Saved rules", xalign=0)
        existing_heading.add_css_class("heading")
        root.append(existing_heading)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(130)
        scroll.set_vexpand(True)
        rule_rows = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
        )
        scroll.set_child(rule_rows)
        root.append(scroll)

        save_button.connect(
            "clicked",
            self._on_save_device_rule,
            window,
            device_names,
            profile_slugs,
            device_dropdown,
            profile_dropdown,
            mode_dropdown,
            rule_rows,
        )
        root.append(save_button)

        close_button = Gtk.Button(label="Close")
        close_button.connect("clicked", lambda _b: window.close())
        root.append(close_button)

        window.set_content(root)
        self._render_device_rules_list(rule_rows, rules, devices)
        window.present()

    def _render_device_rules_list(self, container, rules, devices=()) -> None:
        child = container.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            container.remove(child)
            child = following

        descriptions = {
            d.get("name"): d.get("description") or d.get("name")
            for d in devices
        }

        if not rules:
            container.append(
                Gtk.Label(label="No device rules saved yet.", xalign=0)
            )
            return

        mode_labels = {
            "suggest": "Suggest profile",
            "ignore": "Ignore device",
            "auto": "Automatic switching",
        }

        for rule in rules:
            row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=8,
            )
            label_text = (
                f"{descriptions.get(rule['device_name'], rule['device_name'])}\n"
                f"{rule.get('profile_slug') or 'No profile'} · "
                f"{mode_labels[rule['mode']]}"
            )
            label = Gtk.Label(label=label_text, xalign=0, wrap=True)
            label.set_hexpand(True)
            row.append(label)

            remove_button = Gtk.Button(label="Remove")
            remove_button.connect(
                "clicked",
                self._on_remove_device_rule,
                rule["device_name"],
                container,
            )
            row.append(remove_button)
            container.append(row)

    def _on_save_device_rule(
        self, _button, window, device_names, profile_slugs,
        device_dropdown, profile_dropdown, mode_dropdown, rule_rows,
    ) -> None:
        device_index = device_dropdown.get_selected()
        profile_index = profile_dropdown.get_selected()
        mode_index = mode_dropdown.get_selected()

        if device_index >= len(device_names):
            self.status_label.set_text(
                "Connect an audio output before creating a device rule."
            )
            return

        device_name = device_names[device_index]
        mode_by_index = {0: "suggest", 1: "ignore", 2: "auto"}
        mode = mode_by_index.get(mode_index)

        if mode is None:
            self.status_label.set_text("Choose a valid rule mode.")
            return

        profile_slug = ""
        if mode != "ignore":
            if profile_index >= len(profile_slugs):
                self.status_label.set_text("Choose a valid saved profile.")
                return
            profile_slug = profile_slugs[profile_index]
            config = core.load_config(profile_slug)
            if config is None:
                self.status_label.set_text("The selected profile is invalid.")
                return
            if not any(
                speaker.sink_name == device_name
                for speaker in config.speakers
            ):
                self.status_label.set_text(
                    "That profile does not include this output. "
                    "Choose a profile configured for this device."
                )
                return

        pending = {
            "device_name": device_name,
            "profile_slug": profile_slug,
            "mode": mode,
        }

        if mode == "auto":
            dialog = Adw.AlertDialog(
                heading="Enable Automatic Profile Switching?",
                body=(
                    "When this specific device connects, Audio Profile "
                    "Manager will apply the selected profile without another "
                    "prompt. This can change active audio routing. Continue?"
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("enable-auto", "Enable Automatic Switching")
            dialog.set_response_appearance(
                "enable-auto", Adw.ResponseAppearance.DESTRUCTIVE
            )
            dialog.connect(
                "response",
                self._on_confirm_auto_device_rule,
                pending,
                window,
                rule_rows,
            )
            dialog.present(window)
            return

        self._persist_device_rule(pending, window, rule_rows)

    def _on_confirm_auto_device_rule(
        self, _dialog, response, pending, window, rule_rows
    ) -> None:
        if response != "enable-auto":
            self.status_label.set_text(
                "Automatic switching was not enabled."
            )
            return
        self._persist_device_rule(pending, window, rule_rows)

    def _persist_device_rule(self, pending, window, rule_rows) -> None:
        try:
            rules = device_rules.load_rules()
            rules = [
                item for item in rules
                if item["device_name"] != pending["device_name"]
            ]
            rules.append(pending)
            device_rules.save_rules(rules)
            devices = core.get_available_sinks() or []
            self._render_device_rules_list(rule_rows, rules, devices)
            self.status_label.set_text(
                f"Saved device rule for '{pending['device_name']}'."
            )
        except Exception as exc:
            self.status_label.set_text(f"Could not save device rule: {exc}")

    def _on_remove_device_rule(
        self, _button, device_name, rule_rows
    ) -> None:
        try:
            rules = [
                item for item in device_rules.load_rules()
                if item["device_name"] != device_name
            ]
            device_rules.save_rules(rules)
            self._render_device_rules_list(
                rule_rows, rules, core.get_available_sinks() or []
            )
            self.status_label.set_text(
                f"Removed device rule for '{device_name}'."
            )
        except Exception as exc:
            self.status_label.set_text(f"Could not remove device rule: {exc}")


    def _cycle_profile_from_shortcut(self) -> None:
        """Apply the next saved profile after an explicit keyboard action."""
        profiles = core.list_profiles()
        if not profiles:
            self._report_global_shortcut_status("No saved profiles are available.")
            return

        active = core.list_running_profiles()
        current = active[0] if active else self.current_slug
        try:
            index = profiles.index(current)
        except ValueError:
            index = -1

        next_slug = profiles[(index + 1) % len(profiles)]
        self._apply_profile_from_tray(next_slug)

    def _report_global_shortcut_status(self, message: str) -> None:
        print(f"Audio Profile Manager: {message}")

    def _stop_from_tray(self) -> None:
        self._on_stop_routing(None)

    def _quit_from_tray(self) -> None:
        """Exit explicitly from the tray menu."""
        if self.win:
            self.win.set_visible(False)
        if getattr(self, "_global_shortcuts", None):
            self._global_shortcuts.close()
        if getattr(self, "_tray", None):
            self._tray.close()
        if getattr(self, "_tray_hold_active", False):
            self._tray_hold_active = False
            self.release()
        self.quit()

    def _apply_profile_from_tray(self, slug: str) -> None:
        """Apply a saved profile selected explicitly from the tray menu."""
        try:
            saved = core.load_config(slug)
            if saved is None:
                self.status_label.set_text(
                    f"Could not load saved profile '{slug}'."
                )
                return

            # Preserve edits to the profile currently open in the editor
            # before switching the editor to another saved profile.
            if slug != self.current_slug:
                core.save_config(self.config)

            self.current_slug = slug
            self.config = saved
            self._refresh_profile_model()
            self._select_current_profile_in_combo()
            self.name_entry.set_text(self.config.name)
            self._sync_master_volume_control()
            self._rebuild_speaker_list()
            self._refresh_autostart_state()
            self._update_status()

            # A profile-menu click is an explicit apply action.
            self._on_apply_profile(None)
        except Exception as exc:
            self.status_label.set_text(
                f"Could not apply profile from tray: {exc}"
            )

    def _on_apply_profile(self, _btn: Gtk.Button) -> None:
        """Apply the selected profile; switch away from any active dev profile."""
        if not self.config.speakers:
            self.status_label.set_text("Add at least one output before applying this profile.")
            return

        # Persist first, then work from the saved snapshot in the worker thread.
        try:
            core.save_config(self.config)
            config_to_apply = core.load_config(self.current_slug)
            if config_to_apply is None:
                config_to_apply = self.config
        except Exception as exc:
            self.status_label.set_text(f"Could not save profile: {exc}")
            return

        # Validate targets before stopping a currently-working profile.
        unavailable = [
            speaker.label or speaker.sink_name
            for speaker in config_to_apply.speakers
            if core.resolve_sink(speaker.sink_name) is None
        ]
        if unavailable:
            self.status_label.set_text(
                "Output unavailable: " + ", ".join(unavailable)
                + ". Connect it, then apply again. Existing routing was left alone."
            )
            return

        self.apply_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(False)
        self.status_label.set_text("Applying profile…")

        def do_start():
            try:
                # This is the isolated development state directory, not the old service.
                core.stop_all(quiet=True)
                core.start(config_to_apply)
                GLib.idle_add(self._on_start_done, None)
            except Exception as exc:
                GLib.idle_add(self._on_start_done, str(exc))

        threading.Thread(target=do_start, daemon=True).start()

    def _on_stop_routing(self, _btn: Gtk.Button) -> None:
        """Stop development routing without changing the selected profile."""
        if not core.list_running_profiles():
            self._update_status()
            return

        self.apply_btn.set_sensitive(False)
        self.stop_btn.set_sensitive(False)
        self.status_label.set_text("Stopping audio routing…")

        def do_stop():
            try:
                core.stop_all(quiet=True)
                GLib.idle_add(self._on_stop_done, None)
            except Exception as exc:
                GLib.idle_add(self._on_stop_done, str(exc))

        threading.Thread(target=do_stop, daemon=True).start()

    def _on_start_done(self, error: str | None) -> None:
        self.apply_btn.set_sensitive(True)
        self._update_status()
        if error:
            self.status_label.set_text(f"Apply failed: {error}")

    def _on_stop_done(self, error: str | None) -> None:
        self.apply_btn.set_sensitive(True)
        self._update_status()
        if error:
            self.status_label.set_text(f"Stop failed: {error}")
        elif not core.list_running_profiles():
            self.status_label.set_text("Audio routing stopped.")

    def _sync_master_volume_control(self) -> None:
        """Reflect the newly selected profile without triggering an audio change."""
        self._updating_master_volume = True
        try:
            self.master_volume_scale.set_value(float(self.config.master_volume_percent))
        finally:
            self._updating_master_volume = False

    def _on_master_volume_changed(self, scale: Gtk.Scale) -> None:
        if self._updating_master_volume:
            return
        value = scale.get_value()
        self.config.master_volume_percent = value
        try:
            core.save_config(self.config)
            if core.is_running(self.current_slug):
                core.set_master_volume(self.current_slug, value)
                self.status_label.set_text(f"Master volume: {round(value)}%")
            else:
                self.status_label.set_text(
                    "Master volume saved. Apply the profile to activate it."
                )
        except Exception as exc:
            self.status_label.set_text(f"Master volume error: {exc}")

    def _on_save(self, _btn: Gtk.Button) -> None:
        core.save_config(self.config)
        self.status_label.set_text("Profile saved.")

    def _on_autostart_toggled(self, row: Adw.SwitchRow, _pspec) -> None:
        if getattr(self, "_toggling_autostart", False):
            return
        try:
            core.set_service_enabled(self.current_slug, row.get_active())
            action = "enabled" if row.get_active() else "disabled"
            self.status_label.set_text(f"Auto-start {action}.")
        except Exception as e:
            self.status_label.set_text(f"Service error: {e}")
            self._toggling_autostart = True
            row.set_active(not row.get_active())
            self._toggling_autostart = False

    def _on_add_speaker(self, _btn: Gtk.Button) -> None:
        """Show a dialog to pick a sink to add."""
        selected = {s.sink_name for s in self.config.speakers}
        available = core.get_available_sinks(list(selected))

        if not available:
            dialog = Adw.AlertDialog(
                heading="No Speakers Available",
                body="All detected audio outputs are already added, or no outputs were found.",
            )
            dialog.add_response("ok", "OK")
            dialog.present(self.win)
            return

        dialog = Adw.AlertDialog(
            heading="Add Speaker",
            body="Select an audio output to add:",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listbox.add_css_class("boxed-list")
        listbox.set_margin_start(12)
        listbox.set_margin_end(12)

        for sink in available:
            row = Adw.ActionRow(
                title=sink["description"],
                subtitle=_friendly_sink_type(sink["name"]),
            )
            row.set_subtitle_lines(1)
            row.set_tooltip_text(sink["name"])
            row.sink_data = sink
            listbox.append(row)

        listbox.select_row(listbox.get_row_at_index(0))

        dialog.set_extra_child(listbox)

        dialog.connect("response", self._on_add_dialog_response, listbox, available)
        dialog.present(self.win)

    def _on_add_dialog_response(
        self,
        dialog: Adw.AlertDialog,
        response: str,
        listbox: Gtk.ListBox,
        available: list[dict],
    ) -> None:
        if response != "add":
            return
        selected_row = listbox.get_selected_row()
        if selected_row is None:
            return

        idx = selected_row.get_index()
        sink = available[idx]

        speaker = core.SpeakerConfig(
            sink_name=sink["name"],
            delay_ms=0,
            label=sink["description"],
        )
        self.config.speakers.append(speaker)
        self._rebuild_speaker_list()

    def remove_speaker(self, index: int) -> None:
        """Remove a speaker from the config and rebuild UI."""
        if 0 <= index < len(self.config.speakers):
            self.config.speakers.pop(index)
            self._rebuild_speaker_list()


def main() -> None:
    app = MultiOutputApp()
    app.run()


if __name__ == "__main__":
    main()
