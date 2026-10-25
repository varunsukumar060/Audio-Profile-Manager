#!/usr/bin/bash
# Build an isolated development RPM. This script never installs the RPM.

main() {
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    NAME="audio-profile-manager-dev"
    SPEC_TEMPLATE="$ROOT/packaging/fedora/${NAME}.spec"
    DESKTOP="$ROOT/packaging/fedora/${NAME}.desktop"
    LAUNCHER="$ROOT/packaging/fedora/${NAME}-launcher"

    VERSION="${VERSION:-$(sed -n 's/^Version:[[:space:]]*//p' "$SPEC_TEMPLATE" | head -n 1)}"

    if [[ -z "$VERSION" || ! "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
        echo "Invalid RPM version: ${VERSION:-<empty>}"
        return 2
    fi

    for file in "$SPEC_TEMPLATE" "$DESKTOP" "$LAUNCHER" "$ROOT/LICENSE" \
                "$ROOT/README.md" "$ROOT/multi_output/gui.py"; do
        if [[ ! -f "$file" ]]; then
            echo "Required packaging input is missing: $file"
            return 2
        fi
    done

    if [[ "${1:-}" == "--check" ]]; then
        echo "Checking Fedora RPM packaging inputs..."
        if command -v rpmspec >/dev/null 2>&1; then
            if ! rpmspec -P "$SPEC_TEMPLATE" >/dev/null; then
                echo "RPM spec validation failed."
                return 1
            fi
            echo "RPM spec validation passed."
        else
            echo "rpmspec is unavailable; spec parsing will be checked during the build."
        fi

        if command -v desktop-file-validate >/dev/null 2>&1; then
            if ! desktop-file-validate "$DESKTOP"; then
                echo "Desktop entry validation failed."
                return 1
            fi
        else
            echo "desktop-file-validate is unavailable; desktop validation will run in the RPM build."
        fi

        echo "Packaging files are present."
        return 0
    fi

    for tool in rpmbuild tar git python3 sed; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "Required build tool not found: $tool"
            echo "On Fedora, install the build tools with:"
            echo "  sudo dnf install rpm-build rpmdevtools python3-devel desktop-file-utils"
            return 2
        fi
    done

    TOPDIR="${RPM_TOPDIR:-$HOME/rpmbuild}"
    mkdir -p "$TOPDIR/BUILD" "$TOPDIR/BUILDROOT" \
        "$TOPDIR/RPMS" "$TOPDIR/SOURCES" "$TOPDIR/SPECS" "$TOPDIR/SRPMS"

    SOURCE_ARCHIVE="$TOPDIR/SOURCES/${NAME}-${VERSION}.tar.gz"
    BUILD_SPEC="$TOPDIR/SPECS/${NAME}.spec"
    RAW_LIST="$(mktemp)"
    PACKAGE_LIST="$(mktemp)"

    if ! git -C "$ROOT" ls-files -co --exclude-standard -z > "$RAW_LIST"; then
        echo "Unable to enumerate repository source files."
        rm -f "$RAW_LIST" "$PACKAGE_LIST"
        return 1
    fi

    python3 - "$ROOT" "$RAW_LIST" "$PACKAGE_LIST" <<'PYFILTER'
import os
import sys
from pathlib import Path

root, raw_path, output_path = sys.argv[1:]
raw = Path(raw_path).read_bytes().split(b"\0")
excluded = {
    b".git", b"backups", b"rpmbuild", b"build", b"dist",
    b".venv", b".pytest_cache", b".mypy_cache",
}
kept = []

for item in raw:
    if not item:
        continue
    parts = item.split(b"/")
    if any(part in excluded for part in parts):
        continue
    if b"__pycache__" in parts or item.endswith((b".pyc", b".pyo")):
        continue
    try:
        name = os.fsdecode(item)
        if not os.path.isfile(os.path.join(root, name)):
            continue
    except (OSError, ValueError):
        continue
    kept.append(item)

Path(output_path).write_bytes(b"\0".join(kept) + (b"\0" if kept else b""))
PYFILTER

    FILTER_RESULT=$?
    if [[ "$FILTER_RESULT" -ne 0 || ! -s "$PACKAGE_LIST" ]]; then
        echo "Could not prepare the RPM source file list."
        rm -f "$RAW_LIST" "$PACKAGE_LIST"
        return 1
    fi

    if ! tar -czf "$SOURCE_ARCHIVE" \
        --transform="s,^,${NAME}-${VERSION}/," \
        -C "$ROOT" --null -T "$PACKAGE_LIST"; then
        echo "Failed to create the RPM source archive."
        rm -f "$RAW_LIST" "$PACKAGE_LIST"
        return 1
    fi

    rm -f "$RAW_LIST" "$PACKAGE_LIST"

    if ! sed "s/^Version:[[:space:]].*/Version:        ${VERSION}/" \
        "$SPEC_TEMPLATE" > "$BUILD_SPEC"; then
        echo "Could not prepare the RPM spec file."
        return 1
    fi

    echo
    echo "===== AUDIO PROFILE MANAGER — FEDORA RPM BUILD ====="
    echo "Package: ${NAME}"
    echo "Version: ${VERSION}"
    echo "Source:  ${SOURCE_ARCHIVE}"
    echo "Spec:    ${BUILD_SPEC}"
    echo "Build tree: ${TOPDIR}"
    echo
    echo "The RPM build's %check section will run the unit tests and packaging checks."
    echo "The RPM will be built, not installed."

    rpmbuild -ba "$BUILD_SPEC"
    BUILD_RESULT=$?

    if [[ "$BUILD_RESULT" -eq 0 ]]; then
        echo
        echo "===== RPM BUILD SUCCESSFUL ====="
        find "$TOPDIR/RPMS" -type f \
            -name "${NAME}-${VERSION}-*.rpm" -print
        find "$TOPDIR/SRPMS" -type f \
            -name "${NAME}-${VERSION}-*.src.rpm" -print
        echo
        echo "Nothing has been installed or activated."
        return 0
    fi

    echo
    echo "RPM build failed with status ${BUILD_RESULT}."
    echo "The RPM was not installed. Review the build output above."
    return "$BUILD_RESULT"
}

main "$@"
