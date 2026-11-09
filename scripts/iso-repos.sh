#!/usr/bin/env bash
# Print the --force-repo arguments bootc-image-builder needs to build the
# installer ISO for IMAGE (run as root, like the ISO build itself).
#
#   scripts/iso-repos.sh localhost/firox:latest
#
# Why: since image-builder v89 (October 2026), `--type iso` no longer takes
# the installer's packages from the image's own repositories but from a list
# built into bootc-image-builder. For a Fedora version that list doesn't
# cover properly, every package comes back "No match for argument" (glibc,
# systemd, grub2-…). --force-repo replaces that list, so we point it at
# Fedora's own repositories for the version the image is built on.
#
# The version is read from the image's /etc/os-release. Uses the release +
# updates repositories, or development/ for a Fedora that isn't released
# yet. Override with ISO_REPOS="URL URL…" (base URLs of rpm repositories).
#
# Note: --force-repo repositories are used without GPG checks (bootc-image-
# builder has no way to take keys for them). They're Fedora's own servers,
# over HTTPS; set ISO_FEDORA_MIRROR to use a mirror you trust instead.
set -euo pipefail

image="${1:?usage: iso-repos.sh IMAGE}"
mirror="${ISO_FEDORA_MIRROR:-https://dl.fedoraproject.org/pub/fedora/linux}"
mirror="${mirror%/}"

repos=()
if [ -n "${ISO_REPOS:-}" ]; then
    read -r -a repos <<<"$ISO_REPOS"
else
    info="$(podman run --rm --entrypoint /bin/sh "$image" -c \
        '. /etc/os-release; echo "$ID $VERSION_ID $(uname -m)"')" || {
        echo "iso-repos: couldn't read /etc/os-release from $image" >&2
        exit 1
    }
    read -r id ver arch <<<"$info"
    if [ "$id" != fedora ]; then
        echo "iso-repos: $image is $id, not Fedora; set ISO_REPOS" >&2
        exit 1
    fi
    exists() { curl -fsSL -o /dev/null --max-time 30 "$1/repodata/repomd.xml"; }
    if exists "$mirror/releases/$ver/Everything/$arch/os"; then
        repos+=("$mirror/releases/$ver/Everything/$arch/os/")
        exists "$mirror/updates/$ver/Everything/$arch" && repos+=("$mirror/updates/$ver/Everything/$arch/")
    elif exists "$mirror/development/$ver/Everything/$arch/os"; then
        repos+=("$mirror/development/$ver/Everything/$arch/os/")
    else
        echo "iso-repos: no Fedora $ver repositories found under $mirror" >&2
        exit 1
    fi
    echo "iso-repos: Fedora $ver ($arch): ${repos[*]}" >&2
fi

for r in "${repos[@]}"; do
    printf -- '--force-repo=%s\n' "$r"
done
