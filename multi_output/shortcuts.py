"""Global keyboard shortcuts using the XDG desktop portal.

The desktop environment owns shortcut assignment and conflict handling.
This module never starts or stops audio routing by itself.
"""
from __future__ import annotations

import uuid

from gi.repository import Gio, GLib


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"


class GlobalShortcutManager:
    """Register global shortcuts and dispatch explicit user actions."""

    def __init__(self, *, on_show, on_next_profile, on_stop, on_status):
        self.callbacks = {
            "show_window": on_show,
            "next_profile": on_next_profile,
            "stop_routing": on_stop,
        }
        self.on_status = on_status
        self.connection = None
        self.session_handle = None
        self._request_watchers = {}
        self._activation_subscription = 0
        self._closed = False
        self.ready = False

    @staticmethod
    def _unwrap(value):
        return value.unpack() if isinstance(value, GLib.Variant) else value

    def _status(self, message):
        self.on_status(message)

    def start(self):
        """Begin portal setup; failures leave the rest of the app usable."""
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._activation_subscription = self.connection.signal_subscribe(
                PORTAL,
                GLOBAL_INTERFACE,
                "Activated",
                PORTAL_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                self._on_activated,
                None,
            )

            token = "apm_" + uuid.uuid4().hex
            options = {
                "handle_token": GLib.Variant("s", token + "_request"),
                "session_handle_token": GLib.Variant("s", token + "_session"),
            }
            parameters = GLib.Variant("(a{sv})", (options,))
            reply = self.connection.call_sync(
                PORTAL,
                PORTAL_PATH,
                GLOBAL_INTERFACE,
                "CreateSession",
                parameters,
                GLib.VariantType.new("(o)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            request_path, = reply.unpack()
            self._watch_request(request_path, self._on_session_created)
            self._status(
                "Waiting for the desktop to initialize global shortcuts."
            )
        except Exception as exc:
            self._status(f"Global shortcuts unavailable: {exc}")

    def _watch_request(self, request_path, callback):
        subscription = self.connection.signal_subscribe(
            PORTAL,
            REQUEST_INTERFACE,
            "Response",
            request_path,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_request_response,
            None,
        )
        self._request_watchers[request_path] = (subscription, callback)

    def _on_request_response(
        self, connection, _sender, path, _interface, _signal, parameters, _data
    ):
        watcher = self._request_watchers.pop(path, None)
        if watcher is None:
            return

        subscription, callback = watcher
        connection.signal_unsubscribe(subscription)

        try:
            response, results = parameters.unpack()
            callback(response, results)
        except Exception as exc:
            self._status(f"Global shortcut response failed: {exc}")

    def _on_session_created(self, response, results):
        if response != 0:
            self._status("The desktop did not create a global-shortcut session.")
            return

        self.session_handle = self._unwrap(results.get("session_handle"))
        if not self.session_handle:
            self._status("The desktop returned no shortcut session handle.")
            return

        try:
            definitions = [
                (
                    "show_window",
                    {
                        "description": GLib.Variant(
                            "s", "Show Audio Profile Manager"
                        ),
                        "preferred_trigger": GLib.Variant("s", "CTRL+ALT+m"),
                    },
                ),
                (
                    "next_profile",
                    {
                        "description": GLib.Variant(
                            "s", "Apply the next saved audio profile"
                        ),
                        "preferred_trigger": GLib.Variant("s", "CTRL+ALT+n"),
                    },
                ),
                (
                    "stop_routing",
                    {
                        "description": GLib.Variant(
                            "s", "Stop audio routing"
                        ),
                        "preferred_trigger": GLib.Variant(
                            "s", "CTRL+ALT+SHIFT+s"
                        ),
                    },
                ),
            ]

            shortcuts = GLib.Variant("a(sa{sv})", definitions)
            options_variant = GLib.Variant("a{sv}", {})
            parameters = GLib.Variant(
                "(o@a(sa{sv})s@a{sv})",
                (
                    self.session_handle,
                    shortcuts,
                    "",
                    options_variant,
                ),
            )

            reply = self.connection.call_sync(
                PORTAL,
                PORTAL_PATH,
                GLOBAL_INTERFACE,
                "BindShortcuts",
                parameters,
                GLib.VariantType.new("(o)"),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            request_path, = reply.unpack()
            self._watch_request(request_path, self._on_shortcuts_bound)
        except Exception as exc:
            self._status(f"Could not register global shortcuts: {exc}")

    def _on_shortcuts_bound(self, response, results):
        if response != 0:
            self._status(
                "Global shortcut registration was cancelled or declined."
            )
            return

        bound = self._unwrap(results.get("shortcuts", []))
        self.ready = bool(bound)
        if self.ready:
            self._status(
                "Global shortcuts are registered. The desktop may let you "
                "change their key combinations in its shortcut settings."
            )
        else:
            self._status(
                "No global shortcuts were bound. Check desktop shortcut support."
            )

    def _on_activated(
        self, _connection, _sender, _path, _interface, _signal, parameters, _data
    ):
        try:
            session, shortcut_id, _timestamp, _options = parameters.unpack()
            if self._closed or session != self.session_handle:
                return

            callback = self.callbacks.get(shortcut_id)
            if callback is not None:
                GLib.idle_add(self._dispatch, callback)
        except Exception as exc:
            self._status(f"Global shortcut activation failed: {exc}")

    def _dispatch(self, callback):
        if not self._closed:
            try:
                callback()
            except Exception as exc:
                self._status(f"Global shortcut action failed: {exc}")
        return GLib.SOURCE_REMOVE

    def close(self):
        self._closed = True
        self.ready = False

        if self.connection is None:
            return

        if self.session_handle:
            try:
                self.connection.call_sync(
                    PORTAL,
                    self.session_handle,
                    SESSION_INTERFACE,
                    "Close",
                    None,
                    GLib.VariantType.new("()"),
                    Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            except Exception:
                pass

        for subscription, _callback in self._request_watchers.values():
            try:
                self.connection.signal_unsubscribe(subscription)
            except Exception:
                pass
        self._request_watchers.clear()

        if self._activation_subscription:
            try:
                self.connection.signal_unsubscribe(
                    self._activation_subscription
                )
            except Exception:
                pass
            self._activation_subscription = 0
