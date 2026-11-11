# Firox Linux — a Universal Blue image built around Firefox web apps (PWAs),
# Waydroid, and a classic "XP-style" Wayland desktop (labwc + waybar + a
# custom Start menu).
#
# Two variants come from this file:
#   firox               plain build (defaults below)
#   firox-nvidia-open   `just build-nvidia` sets FIROX_VARIANT=nvidia, pins
#                       BASE_IMAGE by digest and points AKMODS_IMAGE at the
#                       ublue-os/akmods-nvidia-open build for that exact kernel

ARG BASE_IMAGE=ghcr.io/ublue-os/base-main:latest
# Prebuilt, signed NVIDIA kernel modules + driver RPMs. The plain build
# doesn't use it, so it just points at the base image (already pulled).
ARG AKMODS_IMAGE=ghcr.io/ublue-os/base-main:latest

FROM ${AKMODS_IMAGE} AS akmods

# Build context: scripts and files are bind-mounted, never copied as layers
FROM scratch AS ctx
COPY build_files /
COPY system_files /system_files

# Universal Blue's desktop-less Fedora Atomic base.
# It already ships the ublue tooling (ujust, flatpak setup, codecs, RPM Fusion).
FROM ${BASE_IMAGE}
ARG FIROX_VARIANT=main
# A hash of build_files/ and system_files/ (and the commit), set by `just
# build`. It's part of the RUN line, so the build step is never reused from
# podman's cache once the Firox files change: the bind-mounted files don't
# reliably invalidate that cache by themselves, which left the NVIDIA image
# shipping the previous build's files under the new commit's tag.
ARG FIROX_SOURCE_HASH=unknown
ARG FIROX_COMMIT=unknown

RUN --mount=type=bind,from=ctx,source=/,target=/ctx \
    --mount=type=bind,from=akmods,source=/,target=/akmods \
    --mount=type=cache,dst=/var/cache \
    --mount=type=cache,dst=/var/log \
    --mount=type=tmpfs,dst=/tmp \
    FIROX_VARIANT=${FIROX_VARIANT} FIROX_SOURCE_HASH=${FIROX_SOURCE_HASH} FIROX_COMMIT=${FIROX_COMMIT} \
    /ctx/build.sh

### LINTING
RUN bootc container lint
