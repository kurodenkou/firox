#!/usr/bin/bash
# Firox Linux image build script. Runs once inside the Containerfile build.
set -ouex pipefail

FEDORA_VERSION="$(rpm -E %fedora)"
echo "Building Firox Linux on Fedora ${FEDORA_VERSION}"

# What this image was built from. The nightly build checks this file before
# publishing, so an image built from stale (cached) files is never pushed.
install -Dm0644 /dev/stdin /usr/share/firox/build-info <<BUILDINFO
commit=${FIROX_COMMIT:-unknown}
source=${FIROX_SOURCE_HASH:-unknown}
variant=${FIROX_VARIANT:-main}
fedora=${FEDORA_VERSION}
built=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BUILDINFO

### ---------------------------------------------------------------------------
### 1. Third-party repos
### ---------------------------------------------------------------------------

# PWAsForFirefox (firefoxpwa) native component
# https://pwasforfirefox.filips.si/installation/
# Keys are pinned in build_files/keys: packagecloud's repo-metadata key, and
# the upstream author's package-signing key (packagecloud no longer serves it):
#   filips <projects@filips.si>, primary 744007D710DDC8E20673F5452D15DC76BD6B710C,
#   signing subkey 912AD9BE47FEB404 (from keys.openpgp.org / keyserver.ubuntu.com)
install -Dm0644 /ctx/keys/firefoxpwa-repo.asc /etc/pki/rpm-gpg/RPM-GPG-KEY-firefoxpwa-repo
install -Dm0644 /ctx/keys/firefoxpwa-packages.asc /etc/pki/rpm-gpg/RPM-GPG-KEY-firefoxpwa
cat > /etc/yum.repos.d/firefoxpwa.repo <<'EOF'
[firefoxpwa]
name=FirefoxPWA
metadata_expire=7d
baseurl=https://packagecloud.io/filips/FirefoxPWA/rpm_any/rpm_any/$basearch
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-firefoxpwa-repo
       file:///etc/pki/rpm-gpg/RPM-GPG-KEY-firefoxpwa
repo_gpgcheck=1
gpgcheck=1
enabled=1
EOF

### ---------------------------------------------------------------------------
### 2. Packages
### ---------------------------------------------------------------------------

PACKAGES=(
    # --- Wayland desktop: compositor, panel, start menu runtime ---
    labwc
    waybar
    python3-pywayland
    gtk3
    gtk-layer-shell
    python3-gobject
    python3-cairo
    xorg-x11-server-Xwayland
    xdg-desktop-portal
    xdg-desktop-portal-wlr
    xdg-desktop-portal-gtk
    wlrctl                       # "Show desktop" (minimise all windows)
    wlr-randr
    kanshi
    wdisplays                    # Display settings GUI
    swayidle
    swaylock                     # fallback lock screen
    gtk-session-lock             # firox-lock (ext-session-lock for GTK)
    python3-pam                  # firox-lock password check
    wlopm
    mako                         # notifications
    libnotify
    fuzzel                       # clipboard history picker (Super+V)
    grim
    slurp
    wl-clipboard
    cliphist                     # clipboard history (firox-clipboard, Super+V)
    wlsunset                     # Night Light (firox-nightlight)
    brightnessctl
    playerctl
    tuned-ppd                    # power profiles (battery popup: Power saver/Balanced/Performance)

    # --- Login screen ---
    greetd
    greetd-selinux
    gtkgreet
    cage

    # --- Desktop icons + file manager ("My Computer") ---
    lxqt-qtplugin
    qt6-qtwayland
    qt6-qtsvg                    # SVG wallpapers on the desktop
    lxqt-archiver
    gvfs
    gvfs-mtp
    gvfs-smb
    gvfs-fuse                    # network places as folders for every program
    rclone                       # Firox Explorer: S3 and Backblaze B2
    xdg-user-dirs
    xdg-utils

    # --- Accessories & Control Panel applets ---
    xdg-terminal-exec            # lets apps like btop/nvtop open in the Command Prompt
    mousepad                     # Notepad
    galculator                   # Calculator
    ristretto                    # Picture viewer
    htop
    btop                         # Task Manager
    nvtop                        # GPU Monitor in a terminal (Task Manager > View)
    mangohud                     # Firox Gaming HUD (firox-hud): the in-game overlay
    mangohud.i686                # ... for 32-bit games too
    vulkan-tools                 # vulkaninfo (System Information), vkcube (Gaming HUD > Try It)
    python3-pillow               # the lock screen's blurred wallpaper
    dmidecode                    # System Information: memory modules (asks for your password)
    vlc                          # Media Player (music, videos, DVDs)
    vlc-plugin-ffmpeg            # VLC's codecs (weak deps are off; uses the base's full ffmpeg)
    fastfetch
    zenity                       # dialogs for shell scripts (zenity --info, --question, --file-selection...)
    rsync                        # Backup and Restore (firox-backup)
    syncthing                    # Sync between Firox computers (firox-sync)
    clamav                       # Virus Scan (firox-virusscan): clamscan
    clamav-freshclam             # ... and its virus definitions, kept up to date

    # --- Screen savers (Control Panel > Screen Saver) ---
    asciiquarium
    cmatrix
    cbonsai
    vte291                       # screen savers on every monitor; firox-terminal
    pavucontrol                  # Sounds and Audio Devices
    alsa-tools                   # hda-verb, for `ujust fix-realtek-audio`
    network-manager-applet       # tray icon
    nm-connection-editor         # Network Connections
    blueman                      # Bluetooth
    system-config-printer        # Printers and Faxes
    gnome-disk-utility           # Disk Management
    mate-polkit                  # polkit auth agent
    gnome-keyring
    gnome-keyring-pam

    # --- Look & feel ---
    papirus-icon-theme
    papirus-icon-theme-dark      # light icons for Modern's dark pop-ups and Run dialog
    breeze-cursor-theme
    adw-gtk3-theme
    dejavu-sans-fonts
    google-noto-sans-fonts
    google-noto-color-emoji-fonts
    fontawesome-6-free-fonts

    # --- Firefox + web apps ---
    firefox
    firefoxpwa

    # --- Android apps ---
    waydroid
    python3-pyclip               # Waydroid shares the clipboard with Android
)

dnf5 install -y --setopt=install_weak_deps=False "${PACKAGES[@]}"

# Don't leave third-party repos enabled on the deployed system; updates come
# from rebuilding the image.
sed -i 's/^enabled=1/enabled=0/' /etc/yum.repos.d/firefoxpwa.repo

# Fedora 45 moved its repo files from /etc/yum.repos.d to
# /usr/share/dnf5/repos.d. bootc-image-builder (`just build-iso`) reads the
# installer's repositories from the image's /etc/yum.repos.d only, so with
# none there every package was "No match for argument". Link them back:
# dnf5 treats a same-named file in /etc as an override of the /usr/share
# one, so nothing is listed twice, and relative links follow Fedora's
# updates to the originals.
for f in /usr/share/dnf5/repos.d/*.repo; do
    [ -e "$f" ] || continue
    name="$(basename "$f")"
    [ -e "/etc/yum.repos.d/$name" ] || ln -s "../../usr/share/dnf5/repos.d/$name" "/etc/yum.repos.d/$name"
done

### ---------------------------------------------------------------------------
### 3. Our files (desktop config, start menu, themes, ...)
### ---------------------------------------------------------------------------

cp -avf /ctx/system_files/. /

chmod 0755 /usr/bin/firox-*

# Compile dconf defaults (GTK4/libadwaita apps read these)
dconf update || true

# Make the custom labwc session the only one greetd offers
rm -f /usr/share/wayland-sessions/labwc.desktop

### ---------------------------------------------------------------------------
### 4. Services
### ---------------------------------------------------------------------------

# Login manager: greetd + gtkgreet. Disable anything else a base might enable.
for dm in gdm sddm lightdm; do
    systemctl disable "${dm}.service" 2>/dev/null || true
done
systemctl enable greetd.service
systemctl set-default graphical.target

# Waydroid container manager (images are downloaded by `ujust setup-waydroid`)
systemctl enable waydroid-container.service

# Sync between Firox computers: Bonjour (Avahi) discovery, Syncthing, and
# firox-sync's pairing port. Nothing listens on them unless Sync is on.
for zone in public home FedoraWorkstation; do
    firewall-offline-cmd --zone="${zone}" --add-service=mdns --add-service=syncthing \
        --add-port=22100/tcp >/dev/null 2>&1 || true
done

# Network in the file manager: let the answers in when it looks for Windows
# PCs and NAS boxes (WS-Discovery via wsdd, and old NetBIOS browsing). Client
# side only: these accept replies from those ports, nothing listens.
for zone in public home FedoraWorkstation; do
    firewall-offline-cmd --zone="${zone}" --add-service=ws-discovery-client \
        --add-service=samba-client >/dev/null 2>&1 || true
done

# Power profiles (tuned, with the power-profiles-daemon D-Bus API)
systemctl enable tuned.service tuned-ppd.service

# Virus definitions for Virus Scan: freshclam downloads them (about 200 MB
# the first time) and checks for new ones every two hours
systemctl enable clamav-freshclam.service

### ---------------------------------------------------------------------------
### 5. Branding
### ---------------------------------------------------------------------------

# The name the boot menu, boot messages and Settings > System show
sed -i \
    -e 's/^NAME=.*/NAME="Firox Linux"/' \
    -e 's/^PRETTY_NAME=.*/PRETTY_NAME="Firox Linux '"${FEDORA_VERSION}"'"/' \
    -e 's/^LOGO=.*/LOGO=firox-logo/' \
    -e 's/^DEFAULT_HOSTNAME=.*/DEFAULT_HOSTNAME="firox"/' \
    /usr/lib/os-release
grep -q '^DEFAULT_HOSTNAME=' /usr/lib/os-release || echo 'DEFAULT_HOSTNAME="firox"' >> /usr/lib/os-release

# Where this image's updates come from. The installer ISO's %post reads it
# (disk_config/iso.toml), so a system installed from an NVIDIA ISO keeps
# updating from the NVIDIA image instead of switching to the plain one.
FIROX_IMAGE_REPO="${FIROX_IMAGE_REPO:-ghcr.io/kurodenkou/firox}"
if [ "${FIROX_VARIANT:-main}" = nvidia ]; then
    FIROX_IMAGE_REPO="${FIROX_IMAGE_REPO}-nvidia-open"
fi
mkdir -p /usr/share/firox
echo "${FIROX_IMAGE_REPO}:latest" > /usr/share/firox/image-ref

# Boot splash: the Firox logo over Fedora's spinner (theme in
# system_files/usr/share/plymouth/themes/firox; the spinner frames come from
# the spinner theme, keeping our watermark.png)
cp -n /usr/share/plymouth/themes/spinner/*.png /usr/share/plymouth/themes/firox/
plymouth-set-default-theme firox

# The splash and the name are copied into the initramfs: rebuild it (the
# NVIDIA variant rebuilds it anyway, after adding its drivers)
if [ "${FIROX_VARIANT:-main}" != nvidia ]; then
    KERNEL="$(rpm -q kernel --queryformat '%{VERSION}-%{RELEASE}.%{ARCH}\n' | tail -n1)"
    # /root links to /var/roothome, which only exists on a booted system
    roothome_created=0
    if [ ! -e /var/roothome ]; then mkdir -p /var/roothome; roothome_created=1; fi
    /usr/bin/dracut --no-hostonly --kver "${KERNEL}" --reproducible --add ostree \
        -f "/usr/lib/modules/${KERNEL}/initramfs.img"
    chmod 0600 "/usr/lib/modules/${KERNEL}/initramfs.img"
    if [ "${roothome_created}" = 1 ]; then rmdir /var/roothome; fi
fi

### ---------------------------------------------------------------------------
### 6. NVIDIA variant (firox-nvidia-open)
### ---------------------------------------------------------------------------
if [ "${FIROX_VARIANT:-main}" = nvidia ]; then
    /ctx/nvidia.sh
fi

### ---------------------------------------------------------------------------
### 7. Cleanup
### ---------------------------------------------------------------------------
dnf5 clean all
rm -rf /var/lib/dnf /var/cache/* /tmp/* /run/dnf /run/selinux-policy || true
