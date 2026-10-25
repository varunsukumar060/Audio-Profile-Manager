# Audio Profile Manager

Audio Profile Manager is a Linux desktop application for managing saved multi-output audio profiles and routing audio through several PipeWire-compatible output devices. It builds on [pipewire-multi-output](https://github.com/jeremyoverman/pipewire-multi-output).

**Current release: v0.5.0 development release.** This build includes profile reliability and portability features, system tray quick controls, desktop global shortcuts, device-aware rules, and explicit per-application stream routing.

## Features

- Create and switch between saved multi-output profiles.
- Configure per-output delay and volume, plus a profile master volume.
- Import/export individual profiles and back up/restore all saved profiles.
- Use the system tray for quick profile switching, stopping routing, or reopening the main window.
- Register desktop global shortcuts when supported by the desktop's XDG Global Shortcuts portal.
- Create smart device rules to suggest or, with explicit confirmation, switch profiles when outputs appear or disappear.
- Move an individual application's active audio stream to a selected output.
- Validate profile data and write JSON configuration atomically to reduce the risk of partial saves.

## Fedora RPM download

The tagged-release workflow builds a Fedora 44 RPM and attaches it, with a SHA-256 checksum, to the matching GitHub Release when the build succeeds.

- [Open the latest GitHub Release](https://github.com/varunsukumar060/Audio-Profile-Manager/releases/latest)
- [Browse all releases](https://github.com/varunsukumar060/Audio-Profile-Manager/releases)

For v0.5.0, the expected package name is `audio-profile-manager-dev-0.5.0-2.fc44.noarch.rpm`. The download will be available after the GitHub Actions release build succeeds.

Install a downloaded RPM from its download directory:

    sudo dnf install ./audio-profile-manager-dev-0.5.0-2.fc44.noarch.rpm

This package targets Fedora 44. Other Fedora versions may require rebuilding for that distribution.

## Build the Fedora RPM from source

Install the build tools:

    sudo dnf install git tar rpm-build rpmdevtools python3-devel desktop-file-utils

Clone and build:

    git clone https://github.com/varunsukumar060/Audio-Profile-Manager.git
    cd Audio-Profile-Manager
    ./packaging/fedora/build-fedora-rpm.sh

The build script runs the RPM build and its checks, prints the resulting RPM and source-RPM paths, and **does not install the package**. To validate packaging inputs without building:

    ./packaging/fedora/build-fedora-rpm.sh --check

## Launch

After installing the development RPM, open **Audio Profile Manager (Development)** from the KDE application launcher, or run:

    audio-profile-manager-dev

## Keyboard shortcuts

When the desktop's global-shortcuts portal supports registration, the application requests:

- **Ctrl+Alt+M** — open the main window.
- **Ctrl+Alt+N** — apply the next saved profile.
- **Ctrl+Alt+Shift+S** — stop routing.

The desktop may request permission or provide its own shortcut settings. If a shortcut is unavailable, use the tray or main window.

## Configuration isolation and audio safety

The development RPM stores its settings separately from the original installation:

- Configuration and profiles: `~/.config/audio-profile-manager-dev`
- Runtime cache: `~/.cache/audio-profile-manager-dev`

The development build has a separate launcher and does not intentionally import or overwrite the original installation's saved profiles.

**Important:** configuration is isolated, but applying or stopping a profile still changes audio routing in the current user session. Avoid running the development and production applications with the same profile name simultaneously because PipeWire sink names exist in the shared audio session.

Importing a profile or restoring a backup saves configuration; it does not automatically apply imported profiles.

## Development tests

The profile persistence regression tests use Python's standard library and temporary directories. They do not start audio routing or call `pactl` or systemd:

    PYTHONPATH=. python3 -m unittest discover -s tests -v
    python3 -m compileall -q multi_output tests

The automated suite covers profile validation, persistence, import/export, and full backup/restore. Tray controls, global shortcuts, device detection, and application routing also require manual testing on a live PipeWire desktop.

## Release notes

- [v0.2 development notes](CHANGELOG-v0.2.md)
- [v0.3 changelog](CHANGELOG-v0.3.md)
- [v0.5.0 release notes](releases/v0.5.0.md)

## Upstream and license

This is a personal development project based on [jeremyoverman/pipewire-multi-output](https://github.com/jeremyoverman/pipewire-multi-output). See [LICENSE](LICENSE) for license terms.
