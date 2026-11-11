#!/usr/bin/env bash
# Firox Linux nightly build, for a build box in your home lab.
#
# Does what the GitHub workflow (.github/workflows/build.yml) does, with the
# same Justfile recipes: update the checkout, build the standard and NVIDIA
# images, rechunk them, tag them, push them (to ghcr.io and/or a registry on
# your network) and sign them. It can also build installer ISOs.
#
#   scripts/nightly-build.sh              build everything the config asks for
#   scripts/nightly-build.sh --variant nvidia     only one variant
#   scripts/nightly-build.sh --no-push    build (and ISO) but don't publish
#   scripts/nightly-build.sh --dry-run    show what would run
#   scripts/nightly-build.sh --pr 42      test-build a pull request
#   scripts/nightly-build.sh --branch NAME        test-build a branch
#
# A test build (--pr, --branch) only builds: it never publishes or makes ISOs.
#
# Settings: ~/.config/firox-build.conf (or the file in $FIROX_BUILD_CONF);
# copy scripts/nightly-build.conf.example there. Nightly with cron:
#
#   30 2 * * *  /path/to/firox/scripts/nightly-build.sh
#
# Logs go to ~/.local/state/firox-build/ (one per run, kept for 14 days).
# The exit status is non-zero if anything failed, and a short summary is
# printed at the end, so cron's mail (MAILTO) tells you about failures.
# Only one run happens at a time; a run that finds another still going exits.

set -uo pipefail

# ---------------------------------------------------------------- settings --
REPO_URL="https://github.com/kurodenkou/firox.git"
REPO_DIR="$HOME/firox-build"     # a checkout used only by this script (reset each run)
BRANCH="main"
VARIANTS="main nvidia"           # main = firox, nvidia = firox-nvidia-open
RECHUNK=1                        # smaller updates for installed systems, like CI
PUSH_GHCR=1                      # push to ghcr.io/$GHCR_OWNER
GHCR_OWNER="kurodenkou"
GHCR_USER="kurodenkou"
GHCR_TOKEN_FILE="$HOME/.config/firox-ghcr-token"   # a token with write:packages
LOCAL_REGISTRY=""                # e.g. 192.168.1.10:5000 (empty: don't push there)
LOCAL_TLS_VERIFY=true            # false for a plain-HTTP registry
COSIGN_KEY_FILE=""               # cosign.key to sign ghcr.io images (empty: don't sign)
COSIGN_PASSWORD_FILE=""          # its password, if it has one
BUILD_ISO=0                      # 1: build installer ISOs too (needs sudo or root)
ISO_VARIANTS="main"              # which variants get an ISO
ISO_DIR="$HOME/firox-isos"
ISO_KEEP=2                       # newest ISOs to keep per variant
BIB_IMAGE="quay.io/centos-bootc/bootc-image-builder:latest"
LOG_DIR="$HOME/.local/state/firox-build"
LOG_KEEP_DAYS=14
TAG="latest"

CONF="${FIROX_BUILD_CONF:-$HOME/.config/firox-build.conf}"
# shellcheck source=/dev/null
[ -f "$CONF" ] && . "$CONF"

DRY=0
PUSH=1
TEST=""                          # set by --pr / --branch
while [ $# -gt 0 ]; do
    case "$1" in
        --variant) VARIANTS="$2"; ISO_VARIANTS="$2"; shift ;;
        --no-push) PUSH=0 ;;
        --dry-run) DRY=1 ;;
        --pr) TEST="PR #$2"; FETCH="pull/$2/head"; shift ;;
        --branch) TEST="branch $2"; FETCH="$2"; shift ;;
        -h|--help) sed -n '2,26s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

FETCH="${FETCH:-$BRANCH}"
if [ -n "$TEST" ]; then
    # Never publish or replace ISOs from something that isn't merged
    PUSH=0
    BUILD_ISO=0
fi

# ------------------------------------------------------------------ helpers --
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/build-$STAMP.log"
SUMMARY=()
FAILED=0

# One run at a time
exec 9>"$LOG_DIR/lock"
if ! flock -n 9; then
    echo "Another Firox build is still running; not starting a second one."
    exit 0
fi

# Everything goes to the log; the console (cron) gets the step headings and
# the summary
exec 3>&1
exec >>"$LOG" 2>&1

say() { echo "== $(date +%H:%M:%S) $*"; echo "== $*" >&3; }
ok() { SUMMARY+=("ok      $*"); }
fail() { SUMMARY+=("FAILED  $*"); FAILED=1; echo "!! $*" >&3; }
run() {
    echo "+ $*"
    [ "$DRY" = 1 ] && return 0
    "$@"
}
as_root() {
    if [ "$(id -u)" = 0 ]; then run "$@"; else run sudo -n "$@"; fi
}

finish() {
    {
        echo
        echo "Firox nightly build $STAMP on $(hostname)"
        [ "$DRY" = 1 ] && echo "  (dry run: nothing was actually built, pushed or signed)"
        [ -n "$TEST" ] && echo "  (test build of $TEST at ${COMMIT:-?}: not published)"
        printf '  %s\n' "${SUMMARY[@]}"
        echo "Log: $LOG"
    } | tee -a "$LOG" >&3
    find "$LOG_DIR" -name 'build-*.log' -mtime +"$LOG_KEEP_DAYS" -delete 2>/dev/null
    exit "$FAILED"
}

image_name() { [ "$1" = nvidia ] && echo "firox-nvidia-open" || echo "firox"; }

# -------------------------------------------------------------- preflight --
say "Firox nightly build starting (log: $LOG)"
missing=()
for tool in git just podman jq python3 flock; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
if [ -n "$COSIGN_KEY_FILE" ] && ! command -v cosign >/dev/null; then missing+=(cosign); fi
if [ ${#missing[@]} -gt 0 ]; then
    fail "missing programs: ${missing[*]}"
    finish
fi

# ----------------------------------------------------------- update source --
say "Updating $REPO_DIR from $REPO_URL (${TEST:-$BRANCH})"
if [ ! -d "$REPO_DIR/.git" ]; then
    run git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" || { fail "git clone"; finish; }
fi
cd "$REPO_DIR" || { fail "no checkout at $REPO_DIR"; finish; }
# A clean tree, exactly the branch: the image labels and tags carry its commit
if ! { run git fetch --quiet origin "$FETCH" &&
       run git checkout --quiet -B firox-build FETCH_HEAD &&
       run git reset --quiet --hard FETCH_HEAD &&
       run git clean -ffdxq; }; then
    fail "git update"
    finish
fi
COMMIT="$(git rev-parse --short HEAD)"
say "Building commit $COMMIT"

# ----------------------------------------------------------------- logins --
GHCR_OK=0
if [ "$PUSH" = 1 ] && [ "$PUSH_GHCR" = 1 ]; then
    if [ -r "$GHCR_TOKEN_FILE" ]; then
        if [ "$DRY" = 1 ] || podman login --username "$GHCR_USER" --password-stdin ghcr.io <"$GHCR_TOKEN_FILE" >/dev/null; then
            GHCR_OK=1
        else
            fail "ghcr.io login (check $GHCR_TOKEN_FILE)"
        fi
    else
        fail "ghcr.io: no token file at $GHCR_TOKEN_FILE"
    fi
fi

# ------------------------------------------------------------------ build --
declare -A PUSHED_REF BUILT
for variant in $VARIANTS; do
    img="$(image_name "$variant")"
    say "[$variant] building $img:$TAG"
    if [ "$variant" = nvidia ]; then
        run just build-nvidia "$img" "$TAG" || { fail "$img: build"; continue; }
    elif [ "$variant" = main ]; then
        run just build "$img" "$TAG" || { fail "$img: build"; continue; }
    else
        fail "unknown variant '$variant' (use main or nvidia)"; continue
    fi

    # The image must really contain this commit's files: a build step reused
    # from podman's cache once shipped the NVIDIA image with the previous
    # night's files under tonight's tag. build.sh stamps the commit inside.
    if [ "$DRY" != 1 ]; then
        info="$(podman run --rm --pull=never --network none --entrypoint cat "localhost/$img:$TAG" \
                /usr/share/firox/build-info 2>/dev/null || true)"
        if ! grep -qx "commit=$COMMIT" <<<"$info"; then
            fail "$img: the image holds $(grep '^commit=' <<<"$info" || echo 'no build-info'), not $COMMIT (stale build); not published"
            continue
        fi
        say "[$variant] contains commit $COMMIT ($(grep '^source=' <<<"$info"))"
    fi

    if [ "$RECHUNK" = 1 ]; then
        say "[$variant] rechunking"
        run just ostree-rechunk "$img" "$TAG" || { fail "$img: rechunk"; continue; }
    fi

    tags="$(just generate-build-tags "$img" "$TAG" 2>/dev/null | tail -n1)"
    run just tag-images "$img" "$TAG" "$tags" || { fail "$img: tag"; continue; }
    ok "$img built ($COMMIT), tags: $tags"
    BUILT[$variant]=1

    [ "$PUSH" = 1 ] || continue

    if [ "$GHCR_OK" = 1 ]; then
        say "[$variant] pushing to ghcr.io/$GHCR_OWNER/$img"
        digest=""
        pushed=1
        for t in $tags; do
            run podman push --digestfile "$LOG_DIR/digest" "$img:$t" "ghcr.io/$GHCR_OWNER/$img:$t" || { pushed=0; break; }
        done
        if [ "$pushed" = 1 ]; then
            ok "$img pushed to ghcr.io/$GHCR_OWNER"
            PUSHED_REF[$variant]="ghcr.io/$GHCR_OWNER/$img:$TAG"
            [ "$DRY" = 1 ] || digest="$(cat "$LOG_DIR/digest")"
            if [ -n "$COSIGN_KEY_FILE" ]; then
                say "[$variant] signing"
                if [ "$DRY" = 1 ] || COSIGN_PASSWORD="$( [ -n "$COSIGN_PASSWORD_FILE" ] && cat "$COSIGN_PASSWORD_FILE")" \
                        cosign sign -y --new-bundle-format=false --use-signing-config=false \
                        --key "$COSIGN_KEY_FILE" "ghcr.io/$GHCR_OWNER/$img@$digest"; then
                    ok "$img signed"
                else
                    fail "$img: signing"
                fi
            fi
        else
            fail "$img: push to ghcr.io"
        fi
    fi

    if [ -n "$LOCAL_REGISTRY" ]; then
        say "[$variant] pushing to $LOCAL_REGISTRY/$img"
        pushed=1
        for t in $tags; do
            run podman push --tls-verify="$LOCAL_TLS_VERIFY" "$img:$t" "$LOCAL_REGISTRY/$img:$t" || { pushed=0; break; }
        done
        if [ "$pushed" = 1 ]; then
            ok "$img pushed to $LOCAL_REGISTRY"
            [ -n "${PUSHED_REF[$variant]:-}" ] || PUSHED_REF[$variant]="$LOCAL_REGISTRY/$img:$TAG"
        else
            fail "$img: push to $LOCAL_REGISTRY"
        fi
    fi
done

# -------------------------------------------------------------------- ISO --
if [ "$BUILD_ISO" = 1 ]; then
    if [ "$(id -u)" != 0 ] && ! sudo -n true 2>/dev/null; then
        fail "ISO: needs root or passwordless sudo (see the example config)"
    else
        mkdir -p "$ISO_DIR"
        for variant in $ISO_VARIANTS; do
            img="$(image_name "$variant")"
            if [ -z "${BUILT[$variant]:-}" ]; then
                fail "$img: no ISO, the image wasn't built tonight"
                continue
            fi
            say "[$variant] building the installer ISO"
            # bootc-image-builder reads the image from root's container
            # storage: the one just built (as root), or the one just pushed
            if [ "$(id -u)" = 0 ]; then
                ref="localhost/$img:$TAG"
            elif [ -n "${PUSHED_REF[$variant]:-}" ]; then
                ref="${PUSHED_REF[$variant]}"
                tls=()
                [[ "$ref" == "$LOCAL_REGISTRY"/* ]] && tls=(--tls-verify="$LOCAL_TLS_VERIFY")
                as_root podman pull "${tls[@]}" "$ref" || { fail "$img: ISO (pulling $ref as root)"; continue; }
            else
                ref="localhost/$img:$TAG"
                as_root podman image scp "$(id -un)@localhost::$img:$TAG" "root@localhost::" ||
                    { fail "$img: ISO (copying the image to root)"; continue; }
            fi
            out="$(mktemp -d -p "$REPO_DIR" _iso.XXXXXX)"
            # Like `just build-iso`, but without -it, so it runs from cron
            if as_root podman run --rm --privileged --pull=newer --net=host \
                    --security-opt label=type:unconfined_t \
                    -v "$REPO_DIR/disk_config/iso.toml:/config.toml:ro" \
                    -v "$out:/output" \
                    -v /var/lib/containers/storage:/var/lib/containers/storage \
                    "$BIB_IMAGE" --type iso --use-librepo=True --rootfs=btrfs "$ref"; then
                iso="$ISO_DIR/$img-$(date +%Y%m%d)-$COMMIT.iso"
                if [ "$DRY" = 1 ] || as_root mv -f "$out/bootiso/install.iso" "$iso"; then
                    as_root chown "$(id -u):$(id -g)" "$iso" 2>/dev/null
                    ok "$img ISO: $iso"
                    # Keep the newest $ISO_KEEP
                    ls -1t "$ISO_DIR/$img"-[0-9]*.iso 2>/dev/null | tail -n +"$((ISO_KEEP + 1))" | xargs -r rm -f
                else
                    fail "$img: ISO (no install.iso produced)"
                fi
            else
                fail "$img: ISO build"
            fi
            as_root rm -rf "$out"
        done
    fi
fi

# ---------------------------------------------------------------- tidy up --
say "Removing old local images"
for variant in $VARIANTS; do
    img="$(image_name "$variant")"
    # Keep :latest; drop older date/commit tags from earlier nights
    podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null |
        grep -E "^(localhost/)?$img:" | grep -v ":$TAG\$" | grep -v -- "-$COMMIT\$" | grep -v ":$(date +%Y%m%d)" |
        while read -r old; do run podman untag "$old"; done
done
run podman image prune -f >/dev/null
[ "$DRY" = 1 ] || podman logout ghcr.io >/dev/null 2>&1

finish
