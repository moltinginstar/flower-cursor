#!/usr/bin/env sh
# Install the flower cursor and show how to turn it on in Kitty.
set -eu

REPO="https://github.com/moltinginstar/flower-cursor"
DIR="${FLOWER_CURSOR_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/flower-cursor}"
KITTY_CONFIG="${KITTY_CONFIG_DIRECTORY:-${XDG_CONFIG_HOME:-$HOME/.config}/kitty}/kitty.conf"
BEGIN="# >>> flower-cursor >>>"
END="# <<< flower-cursor <<<"

species=""
configure=0
uninstall=0
dry_run=0

usage() {
    cat << 'USAGE'
Usage: install.sh [options]

  --species NAME   grow NAME instead of the default flower (or cycle/random)
  --configure      write the Kitty settings for me, keeping a backup
  --uninstall      take those settings back out again
  --dry-run        say what would change, change nothing
  -h, --help       show this help
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --species) species="${2-}"; [ -n "$species" ] || { echo "install.sh: --species needs a name" >&2; exit 2; }; shift 2 ;;
        --species=*) species="${1#--species=}"; shift ;;
        --configure) configure=1; shift ;;
        --uninstall) uninstall=1; shift ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
    esac
done

have() { command -v "$1" > /dev/null 2>&1; }

# Everything here is a line of text; the only thing that ever writes it out is
# `apply`, so --dry-run has one place to stop rather than a flag in every step.
block() {
    echo "$BEGIN"
    echo "shell $DIR/kitty/flower-cursor-shell"
    [ -n "$species" ] && echo "env FLOWER_CURSOR_SPECIES=$species"
    echo "$END"
}

# Drop the flower-cursor block and keep every other line. A block is only a block once
# its end marker turns up: if the user has removed or reflowed that line, the
# held-back lines are printed again rather than swallowed, because otherwise a
# --uninstall or a second --configure would silently delete the whole remainder
# of the user's kitty.conf.
without_block() {
    [ -f "$KITTY_CONFIG" ] || return 0
    awk -v b="$BEGIN" -v e="$END" '
        skip == 1 {
            held = held $0 "\n"
            if ($0 == e) { skip = 0; held = "" }
            next
        }
        $0 == b { skip = 1; held = $0 "\n"; next }
        { print }
        END {
            if (skip == 1) {
                printf "%s", held
                print "install.sh: warning: no end marker after " b " in your kitty.conf;" > "/dev/stderr"
                print "  leaving those lines alone. Remove them by hand if they came from flower-cursor." > "/dev/stderr"
            }
        }
    ' "$KITTY_CONFIG"
}

apply() {
    if [ "$dry_run" -eq 1 ]; then
        echo "Would write to $KITTY_CONFIG:"
        echo
        cat
        return 0
    fi
    mkdir -p "$(dirname "$KITTY_CONFIG")"
    if [ -f "$KITTY_CONFIG" ]; then
        # Two runs in the same second must not leave one backup: the first one
        # is the config as the user wrote it, and that is the one worth having.
        backup="$KITTY_CONFIG.backup-$(date +%Y%m%d%H%M%S)"
        attempt=1
        while [ -e "$backup" ]; do
            backup="$KITTY_CONFIG.backup-$(date +%Y%m%d%H%M%S)-$attempt"
            attempt=$((attempt + 1))
        done
        cp "$KITTY_CONFIG" "$backup"
        echo "Backed up your old config to $backup."
    fi
    cat > "$KITTY_CONFIG.flower-cursor.tmp"
    mv "$KITTY_CONFIG.flower-cursor.tmp" "$KITTY_CONFIG"
}

restart_notice() {
    cat << 'NOTICE'

Kitty reads its config when it starts, so restart Kitty (or reload the config
with ctrl+cmd+, on macOS, ctrl+shift+f5 elsewhere) to see the change. Windows
that are already open keep the shell they were started with.
NOTICE
}

if [ "$uninstall" -eq 1 ]; then
    if ! grep -qxF "$BEGIN" "$KITTY_CONFIG" 2> /dev/null; then
        echo "Nothing to remove: $KITTY_CONFIG has no flower-cursor settings in it."
        exit 0
    fi
    without_block | apply
    [ "$dry_run" -eq 1 ] || echo "Removed the flower-cursor settings from $KITTY_CONFIG."
    [ -d "$DIR" ] && echo "The files are still in $DIR; delete that directory to finish up."
    restart_notice
    exit 0
fi

have kitty || echo "install.sh: warning: no kitty on your PATH; this only does anything in Kitty." >&2
have uv || { echo "install.sh: uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

# Run from a checkout, install that checkout. Run from a pipe, fetch one.
# `sh install.sh` from inside the checkout leaves no slash in $0, so "." is a
# candidate too; both are confirmed by files only this project has, so a pipe
# run from some unrelated project's directory still fetches a real checkout.
is_checkout() { [ -f "$1/pyproject.toml" ] && [ -f "$1/kitty/flower-cursor-shell" ]; }
here=""
case "${0-}" in
    */*) candidate="${0%/*}" ;;
    *) candidate="." ;;
esac
is_checkout "$candidate" && here="$(CDPATH= cd -- "$candidate" && pwd)"
if [ -n "$here" ]; then
    DIR="$here"
    echo "Installing from $DIR."
elif [ -d "$DIR/.git" ]; then
    echo "Updating $DIR."
    [ "$dry_run" -eq 1 ] || git -C "$DIR" pull --ff-only
else
    have git || { echo "install.sh: git is required to fetch $REPO" >&2; exit 1; }
    echo "Cloning $REPO into $DIR."
    [ "$dry_run" -eq 1 ] || git clone --depth 1 "$REPO" "$DIR"
fi

if [ "$dry_run" -eq 1 ]; then
    echo "Would run: uv sync --directory $DIR --no-dev --inexact"
else
    uv sync --directory "$DIR" --no-dev --inexact
fi

if [ "$configure" -eq 1 ]; then
    if without_block | grep -qE '^shell[[:space:]]'; then
        echo "install.sh: warning: $KITTY_CONFIG already sets a shell; the flower cursor" >&2
        echo "  goes last and wins. Remove the other line if that is not what you want." >&2
    fi
    { without_block; block; } | apply
    [ "$dry_run" -eq 1 ] || echo "Wrote the flower-cursor settings to $KITTY_CONFIG."
else
    cat << CONFIG

Add this to $KITTY_CONFIG:

$(block | sed 's/^/  /')

Or run this script again with --configure and it will do it for you.
CONFIG
fi

restart_notice
