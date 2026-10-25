"""KDE Plasma-compatible StatusNotifierItem for Audio Profile Manager.

Uses the session D-Bus and the standard StatusNotifierItem/DBusMenu protocols.
It does not start, stop, or change audio routing by itself.
"""
from __future__ import annotations

import sys

from gi.repository import Gio, GLib

from . import core


_ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg type="i" direction="in"/><arg type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
    </method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" direction="in"/>
      <arg type="i" direction="in"/>
      <arg type="as" direction="in"/>
      <arg type="u" direction="out"/>
      <arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" direction="in"/>
      <arg type="as" direction="in"/>
      <arg type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/>
      <arg type="s" direction="in"/>
      <arg type="v" direction="in"/>
      <arg type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" direction="in"/>
      <arg type="a(is)" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" direction="in"/>
      <arg type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" direction="in"/>
      <arg type="ai" direction="out"/>
      <arg type="ai" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <signal name="LayoutUpdated">
      <arg type="u"/><arg type="i"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})"/><arg type="a(ias)"/>
    </signal>
  </interface>
</node>
"""


class TrayController:
    """Expose a tray icon and menu without owning audio-routing state."""

    item_path = "/StatusNotifierItem"
    menu_path = "/StatusNotifierItem/Menu"

    def __init__(self, *, on_open, on_context_menu, on_apply_profile, on_stop, on_quit):
        self.on_open = on_open
        self.on_context_menu = on_context_menu
        self.on_apply_profile = on_apply_profile
        self.on_stop = on_stop
        self.on_quit = on_quit
        self.available = False
        self.connection = None
        self._registrations = []
        self._profile_ids = {}
        self._items_by_id = {}
        self._revision = 1

        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._item_info = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML).interfaces[0]
            self._menu_info = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]

            self._registrations.append(
                self.connection.register_object(
                    self.item_path, self._item_info,
                    self._on_method_call, self._get_item_property, None,
                )
            )
            self._registrations.append(
                self.connection.register_object(
                    self.menu_path, self._menu_info,
                    self._on_method_call, self._get_menu_property, None,
                )
            )

            self._build_menu_tree()
            self.connection.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self.connection.get_unique_name(),)),
                None,
                Gio.DBusCallFlags.NONE,
                2500,
                None,
            )
            self.available = True
        except Exception as exc:
            self.available = False
            print(
                f"Audio Profile Manager: system tray unavailable: {exc}",
                file=sys.stderr,
            )
            for registration in self._registrations:
                try:
                    self.connection.unregister_object(registration)
                except Exception:
                    pass
            self._registrations.clear()

    def _queue(self, callback, *args):
        if callback is not None:
            GLib.idle_add(self._run_callback, callback, args)

    @staticmethod
    def _run_callback(callback, args):
        try:
            callback(*args)
        except Exception as exc:
            print(f"Audio Profile Manager tray action failed: {exc}", file=sys.stderr)
        return GLib.SOURCE_REMOVE

    def _build_menu_tree(self):
        active = set(core.list_running_profiles())
        self._profile_ids = {}

        profile_items = []
        for index, slug in enumerate(core.list_profiles()):
            config = core.load_config(slug)
            if config is None:
                continue
            item_id = 1000 + index
            self._profile_ids[item_id] = slug
            label = config.name
            if slug in active:
                label += " — active"
            profile_items.append({
                "id": item_id,
                "label": label,
                "enabled": True,
                "children": [],
            })

        if not profile_items:
            profile_items = [{
                "id": 999,
                "label": "No valid saved profiles",
                "enabled": False,
                "children": [],
            }]

        stop_enabled = bool(active)
        self._root = {
            "id": 0,
            "label": "",
            "enabled": True,
            "children": [
                {"id": 1, "label": "Open Audio Profile Manager",
                 "enabled": True, "children": []},
                {"id": 2, "label": "Stop Routing",
                 "enabled": stop_enabled, "children": []},
                {"id": 3, "type": "separator", "enabled": True, "children": []},
                {"id": 4, "label": "Apply a saved profile",
                 "enabled": True, "children": profile_items},
                {"id": 5, "label": "Quit Audio Profile Manager",
                 "enabled": True, "children": []},
            ],
        }

        self._items_by_id = {}

        def index_items(item):
            self._items_by_id[item["id"]] = item
            for child in item.get("children", []):
                index_items(child)

        index_items(self._root)

    @staticmethod
    def _properties(item):
        if item.get("type") == "separator":
            return {
                "type": GLib.Variant("s", "separator"),
                "visible": GLib.Variant("b", True),
            }

        props = {
            "label": GLib.Variant("s", item.get("label", "")),
            "enabled": GLib.Variant("b", bool(item.get("enabled", True))),
            "visible": GLib.Variant("b", True),
        }
        if item.get("children"):
            props["children-display"] = GLib.Variant("s", "submenu")
        return props

    def _layout_node(self, item, depth):
        child_nodes = []
        if depth != 0:
            next_depth = depth - 1 if depth > 0 else depth
            child_nodes = [
                GLib.Variant("v", self._layout_node(child, next_depth))
                for child in item.get("children", [])
            ]
        return GLib.Variant(
            "(ia{sv}av)",
            (item["id"], self._properties(item), child_nodes),
        )

    def _on_method_call(
        self, _connection, _sender, object_path, interface_name,
        method_name, parameters, invocation,
    ):
        try:
            if interface_name == "org.kde.StatusNotifierItem":
                if method_name == "ContextMenu":
                    self._queue(self.on_context_menu)
                elif method_name in ("Activate", "SecondaryActivate"):
                    self._queue(self.on_open)
                invocation.return_value(GLib.Variant.new_tuple([]))
                return

            if interface_name != "com.canonical.dbusmenu":
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.UnknownMethod", method_name
                )
                return

            if method_name == "GetLayout":
                parent_id, depth, _property_names = parameters.unpack()
                self._build_menu_tree()
                parent = self._items_by_id.get(parent_id, self._root)
                layout = self._layout_node(parent, depth)
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant("u", self._revision),
                    layout,
                ]))
                return

            if method_name == "GetGroupProperties":
                ids, requested = parameters.unpack()
                rows = []
                for item_id in ids:
                    item = self._items_by_id.get(item_id)
                    if item is not None:
                        props = self._properties(item)
                        rows.append((
                            item_id,
                            {key: value for key, value in props.items()
                             if not requested or key in requested},
                        ))
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant("a(ia{sv})", rows)
                ]))
                return

            if method_name == "GetProperty":
                item_id, prop_name = parameters.unpack()
                item = self._items_by_id.get(item_id)
                props = self._properties(item) if item else {}
                value = props.get(prop_name, GLib.Variant("s", ""))
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant("v", value)
                ]))
                return

            if method_name == "Event":
                item_id, event_name, _data, _timestamp = parameters.unpack()
                self._handle_event(item_id, event_name)
                invocation.return_value(GLib.Variant.new_tuple([]))
                return

            if method_name == "EventGroup":
                events, = parameters.unpack()
                for item_id, event_name, _data, _timestamp in events:
                    self._handle_event(item_id, event_name)
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant("a(is)", [])
                ]))
                return

            if method_name == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
                return

            if method_name == "AboutToShowGroup":
                invocation.return_value(GLib.Variant.new_tuple([
                    GLib.Variant("ai", []),
                    GLib.Variant("ai", []),
                ]))
                return

            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", method_name
            )
        except Exception as exc:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.Failed", str(exc)
            )

    def _handle_event(self, item_id, event_name):
        if event_name != "clicked":
            return
        if item_id == 1:
            self._queue(self.on_open)
        elif item_id == 2:
            self._queue(self.on_stop)
        elif item_id == 5:
            self._queue(self.on_quit)
        elif item_id in self._profile_ids:
            self._queue(self.on_apply_profile, self._profile_ids[item_id])

    def _get_item_property(
        self, _connection, _sender, _path, _interface, property_name
    ):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "audio-profile-manager"),
            "Title": GLib.Variant("s", "Audio Profile Manager"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("u", 0),
            "IconName": GLib.Variant("s", "audio-speakers-symbolic"),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)",
                ("Audio Profile Manager", [], "Audio profile controls",
                 "Open the manager or apply a saved profile"),
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/NO_DBUSMENU"),
        }
        return values.get(property_name)

    def _get_menu_property(
        self, _connection, _sender, _path, _interface, property_name
    ):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("s", ""),
        }
        return values.get(property_name)

    def close(self):
        for registration in self._registrations:
            try:
                self.connection.unregister_object(registration)
            except Exception:
                pass
        self._registrations.clear()
        self.available = False
