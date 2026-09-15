#!/usr/bin/env bash
#
# install.sh -- macOS / Linux installer for gegenschuss_ae_export.
#
# Locates Houdini's hython, runs install_hda.py, and prints the
# HOUDINI_OTLSCAN_PATH line you should add to your shell or houdini.env.
#
# Override hython detection by exporting HYTHON before running:
#     HYTHON=/path/to/hython ./install.sh
#
set -euo pipefail

HERE="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
INSTALL_PY="$HERE/install_hda.py"
DEFAULT_OUT_DIR="$HERE/otls"
DEFAULT_LABEL="inside repo"
SECRETS_AUTHORIZED=0

if [[ ! -f "$INSTALL_PY" ]]; then
    echo "install_hda.py not found next to this script ($HERE)." >&2
    exit 1
fi

# Optional install_secrets override: first non-comment, non-blank line
# is a local default path.  Gitignored, never published.
if [[ -f "$HERE/install_secrets" ]]; then
    SECRET_PATH=$(awk '!/^[[:space:]]*#/ && !/^[[:space:]]*$/ {print; exit}' "$HERE/install_secrets" | tr -d '\r')
    if [[ -n "$SECRET_PATH" ]]; then
        SECRET_PATH="${SECRET_PATH/#\~/$HOME}"
        DEFAULT_OUT_DIR="${SECRET_PATH%/}"
        DEFAULT_LABEL="from install_secrets"
        SECRETS_AUTHORIZED=1
    fi
fi
DEFAULT_OUT_HDA="$DEFAULT_OUT_DIR/gegenschuss_ae_export.hda"

# ----- Choose install location -------------------------------------------
echo "Where should the HDA install?"
echo
echo "  [1] $DEFAULT_OUT_HDA   (default, $DEFAULT_LABEL)"
echo "  [2] Custom path"
echo
read -r -p "Choice [1]: " CHOICE
CHOICE="${CHOICE:-1}"

case "$CHOICE" in
    1)
        OUT_HDA="$DEFAULT_OUT_HDA"
        ;;
    2)
        read -r -p "Path (file or directory): " CUSTOM
        if [[ -z "$CUSTOM" ]]; then
            echo "Empty path; cancelled." >&2
            exit 1
        fi
        # Expand leading ~ to $HOME.
        CUSTOM="${CUSTOM/#\~/$HOME}"
        # Make absolute if relative (relative-to-cwd, not to repo).
        [[ "$CUSTOM" != /* ]] && CUSTOM="$PWD/$CUSTOM"
        # If they gave a directory (or a path without .hda), append the filename.
        if [[ -d "$CUSTOM" || "$CUSTOM" != *.hda ]]; then
            OUT_HDA="${CUSTOM%/}/gegenschuss_ae_export.hda"
        else
            OUT_HDA="$CUSTOM"
        fi
        ;;
    *)
        echo "Invalid choice." >&2
        exit 1
        ;;
esac

# Confirm if path is outside the repo.  Skipped when the user is taking
# the install_secrets default -- they pre-authorized that path by writing
# it into install_secrets.
case "$OUT_HDA" in
    "$HERE"/*) ;;
    *)
        if [[ "$SECRETS_AUTHORIZED" == "1" && "$CHOICE" == "1" ]]; then
            : # secrets-authorized default
        else
            echo
            echo "This will create a file OUTSIDE the repo:"
            echo "  $OUT_HDA"
            read -r -p "Proceed? [y/N]: " YN
            case "$YN" in
                [Yy]|[Yy][Ee][Ss]) ;;
                *) echo "Cancelled."; exit 0 ;;
            esac
        fi
        ;;
esac

OUT_DIR="$(dirname "$OUT_HDA")"

# ----- Replace-existing check -------------------------------------------
if [[ -f "$OUT_HDA" ]]; then
    echo
    echo "File already exists:"
    echo "  $OUT_HDA"
    read -r -p "Replace? [Y/n]: " YN
    case "$YN" in
        [Nn]|[Nn][Oo]) echo "Cancelled."; exit 0 ;;
        *) ;;
    esac
fi

find_hython() {
    if [[ -n "${HYTHON:-}" ]]; then
        echo "$HYTHON"; return 0
    fi
    if command -v hython >/dev/null 2>&1; then
        command -v hython; return 0
    fi
    local found
    case "$(uname -s)" in
        Darwin)
            # /Applications/Houdini/Houdini<ver>/Frameworks/Houdini.framework/Versions/<ver>/Resources/bin/hython
            # That's 8 dirs deep from /Applications/Houdini, so use -maxdepth 9.
            found=$(find /Applications/Houdini -maxdepth 9 -type f -name hython 2>/dev/null \
                    | sort -V | tail -1)
            ;;
        Linux)
            # SideFX recommends /opt/hfs<ver>; some sites use /opt/houdini-*.
            # /opt/hfs21.0/bin/hython is 3 deep; /opt/sidefx/houdini-*/bin/hython is 4.
            found=$(find /opt /usr/local -maxdepth 4 -type f -name hython 2>/dev/null \
                    | sort -V | tail -1)
            ;;
        *)
            found=""
            ;;
    esac
    [[ -n "$found" ]] && { echo "$found"; return 0; }
    return 1
}

HYTHON_BIN=$(find_hython) || {
    cat >&2 <<EOF
Could not find hython.

Set HYTHON to your hython path and re-run, e.g.:
  macOS:   HYTHON=/Applications/Houdini/Houdini21.0.671/Frameworks/Houdini.framework/Versions/Current/Resources/bin/hython ./install.sh
  Linux:   HYTHON=/opt/hfs21.0/bin/hython ./install.sh
EOF
    exit 1
}

mkdir -p "$OUT_DIR"

echo
echo "hython:    $HYTHON_BIN"
echo "script:    $INSTALL_PY"
echo "output:    $OUT_HDA"
echo

"$HYTHON_BIN" "$INSTALL_PY" "$OUT_HDA"
