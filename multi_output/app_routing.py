"""Manual per-application audio stream routing using pactl JSON."""
from __future__ import annotations

import json
import shutil
import subprocess


class RoutingError(RuntimeError):
    """Raised when PipeWire/PulseAudio stream routing is unavailable."""


def _pactl_json(*arguments):
    if not shutil.which("pactl"):
        raise RoutingError("The pactl command was not found.")

    try:
        result = subprocess.run(
            ["pactl", "-f", "json", "list", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired as exc:
        raise RoutingError("The audio server did not respond in time.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RoutingError(detail or "The audio server rejected the request.") from exc
    except OSError as exc:
        raise RoutingError(str(exc)) from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RoutingError("The audio server returned invalid JSON.") from exc

    if not isinstance(data, list):
        raise RoutingError("The audio server returned an unexpected data format.")

    return data


def list_output_sinks():
    """Return available sinks, including virtual multi-output profile sinks."""
    result = []

    for sink in _pactl_json("sinks"):
        if not isinstance(sink, dict):
            continue

        name = sink.get("name")
        index = sink.get("index")
        if not isinstance(name, str) or not name:
            continue

        result.append({
            "index": index,
            "name": name,
            "description": sink.get("description") or name,
        })

    return result


def list_sink_inputs():
    """List application audio streams and their current output destinations."""
    sinks = list_output_sinks()
    sink_by_index = {
        str(sink["index"]): sink
        for sink in sinks
        if sink.get("index") is not None
    }

    streams = []
    for item in _pactl_json("sink-inputs"):
        if not isinstance(item, dict):
            continue

        stream_id = item.get("index")
        if stream_id is None:
            continue

        properties = item.get("properties")
        if not isinstance(properties, dict):
            properties = {}

        def prop(*keys):
            for key in keys:
                value = properties.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""

        app_name = prop(
            "application.name",
            "application.process.binary",
            "application.id",
            "node.name",
        )
        media_name = prop("media.name", "node.description")
        if not app_name:
            app_name = media_name or f"Audio stream {stream_id}"

        try:
            numeric_id = int(stream_id)
        except (TypeError, ValueError):
            continue

        current_sink = sink_by_index.get(str(item.get("sink")))
        streams.append({
            "id": numeric_id,
            "application": app_name,
            "media_name": media_name,
            "current_sink_name": (
                current_sink["name"] if current_sink else ""
            ),
            "current_sink_description": (
                current_sink["description"] if current_sink
                else "Unknown output"
            ),
        })

    return streams


def move_stream(stream_id, sink_name):
    """Move one stream only, following an explicit user action."""
    try:
        numeric_id = int(stream_id)
    except (TypeError, ValueError) as exc:
        raise RoutingError("The selected stream ID is invalid.") from exc

    if numeric_id < 0:
        raise RoutingError("The selected stream ID is invalid.")

    if not isinstance(sink_name, str) or not sink_name or len(sink_name) > 1024:
        raise RoutingError("The selected output name is invalid.")

    available = {sink["name"] for sink in list_output_sinks()}
    if sink_name not in available:
        raise RoutingError(
            "That output is no longer available. Refresh the stream list."
        )

    if not shutil.which("pactl"):
        raise RoutingError("The pactl command was not found.")

    try:
        subprocess.run(
            ["pactl", "move-sink-input", str(numeric_id), sink_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired as exc:
        raise RoutingError("The audio server did not respond in time.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RoutingError(
            detail or "The audio stream could not be moved."
        ) from exc
    except OSError as exc:
        raise RoutingError(str(exc)) from exc
