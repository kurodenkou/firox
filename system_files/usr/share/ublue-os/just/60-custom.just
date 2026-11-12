# Firox Linux recipes (`ujust --choose`, or e.g. `ujust setup-waydroid`)

# Set up Waydroid for Android apps. TYPE is "vanilla" (no Google) or "gapps"
[group('Firox Linux')]
setup-waydroid TYPE="vanilla":
    #!/usr/bin/bash
    set -euo pipefail
    case "{{ TYPE }}" in
        vanilla) SYS=VANILLA ;;
        gapps)   SYS=GAPPS ;;
        *) echo "TYPE must be 'vanilla' or 'gapps'"; exit 1 ;;
    esac
    echo "Downloading the Android image (${SYS}). This is several hundred MB..."
    sudo waydroid init -s "${SYS}" -f \
        -c https://ota.waydro.id/system \
        -v https://ota.waydro.id/vendor
    if command -v firewall-cmd >/dev/null && sudo firewall-cmd --state >/dev/null 2>&1; then
        # Let the Android container reach the network
        sudo firewall-cmd --zone=trusted --add-interface=waydroid0 --permanent
        sudo firewall-cmd --zone=trusted --add-port=53/udp --add-port=67/udp --permanent
        sudo firewall-cmd --reload
    fi
    if [ -e /proc/driver/nvidia/version ]; then
        # Waydroid's GPU acceleration doesn't work with NVIDIA's driver:
        # use software rendering instead
        echo "NVIDIA driver detected: using software rendering for Android."
        printf 'ro.hardware.gralloc=default\nro.hardware.egl=swiftshader\n' \
            | sudo tee -a /var/lib/waydroid/waydroid_base.prop >/dev/null
    fi
    sudo systemctl restart waydroid-container.service
    echo
    echo "Waydroid is ready. Open Start -> Android Apps."
    if [ "${SYS}" = GAPPS ]; then
        echo "To use the Play Store, register this device: ujust waydroid-google-id"
    fi
    read -rp "Press Enter to close" _

# Print the Android ID needed to certify a GAPPS Waydroid install with Google
[group('Firox Linux')]
waydroid-google-id:
    #!/usr/bin/bash
    set -euo pipefail
    echo "Starting Waydroid session (if needed)..."
    waydroid session start >/dev/null 2>&1 & sleep 10
    sudo waydroid shell -- sh -c "sqlite3 /data/data/*/*/gservices.db \"select * from main where name='android_id';\"" \
        | awk -F'|' '{print "Android ID: " $2}'
    echo "Paste the number at https://www.google.com/android/uncertified then restart Waydroid."

# Remove Waydroid's Android image and all its data
[group('Firox Linux')]
reset-waydroid:
    #!/usr/bin/bash
    set -euo pipefail
    read -rp "This deletes all Android apps and data. Continue? [y/N] " ans
    [[ "${ans,,}" == y* ]] || exit 0
    waydroid session stop 2>/dev/null || true
    sudo systemctl stop waydroid-container.service
    sudo rm -rf /var/lib/waydroid ~/.local/share/waydroid
    rm -f ~/.local/share/applications/waydroid.*.desktop
    sudo systemctl start waydroid-container.service
    echo "Waydroid reset. Run 'ujust setup-waydroid' to set it up again."

# Download (or update) the Firefox runtime used by web apps
[group('Firox Linux')]
install-webapp-runtime:
    firefoxpwa runtime install

# Install the Bazaar app store (Flathub), opened from Start > Bazaar
[group('Firox Linux')]
install-app-store:
    #!/usr/bin/bash
    set -euo pipefail
    flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install -y --user flathub io.github.kolunmi.Bazaar
    echo "Installed. Open Start -> Bazaar."
    read -rp "Press Enter to close" _

# Sunshine (the Flathub app, started at login) streams this computer's desktop
# and games to Moonlight on a phone, tablet, TV or another PC
# Install Sunshine to stream games and the desktop to Moonlight
[group('Firox Linux')]
install-sunshine:
    #!/usr/bin/bash
    set -euo pipefail
    APP=dev.lizardbyte.app.Sunshine
    echo "This installs Sunshine (from Flathub), lets it control the mouse, keyboard"
    echo "and game controllers, opens its ports in the firewall and starts it at login."
    read -rp "Continue? [y/N] " ans
    [[ "${ans,,}" == y* ]] || exit 0
    flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
    flatpak install -y --or-update --user flathub "$APP"
    echo
    echo "Setting up input devices for streaming (a password window will appear)..."
    # Sunshine's own helper: udev rules for its virtual mouse, keyboard and
    # controllers. Run again after Sunshine updates if input stops working.
    flatpak run --command=additional-install.sh "$APP"
    echo
    echo "Opening Sunshine's streaming ports in the firewall..."
    zone="$(firewall-cmd --get-default-zone 2>/dev/null || echo FedoraWorkstation)"
    # The web UI (47990) stays local: manage Sunshine from this computer
    sudo firewall-cmd --permanent --zone="$zone" \
        --add-port=47984/tcp --add-port=47989/tcp --add-port=48010/tcp \
        --add-port=47998-48000/udp --add-port=48002/udp --add-port=48010/udp >/dev/null
    sudo firewall-cmd --reload >/dev/null
    # Start at login, through Startup Programs (where it can be unticked). Not
    # Sunshine's systemd user service: that waits for graphical-session.target,
    # which the Firox session doesn't start, so ignore the helper's tip about it.
    mkdir -p ~/.config/autostart
    printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=Sunshine' \
        'Comment=Game and desktop streaming host for Moonlight' \
        "Exec=flatpak run --command=sunshine $APP" "Icon=$APP" 'X-GNOME-Autostart-enabled=true' \
        > ~/.config/autostart/$APP.desktop
    # Screen capture: the desktop portal (screen sharing), as Sunshine's docs
    # recommend for the Flatpak. Its other Linux methods need CAP_SYS_ADMIN
    # (KMS), which a Flatpak can't have ("Failed to gain CAP_SYS_ADMIN"), or
    # GPU buffer sharing that didn't work here. A method you chose in
    # Sunshine's web page is kept; "capture = wlr", suggested earlier, is
    # replaced.
    CONF=~/.var/app/$APP/config/sunshine/sunshine.conf
    mkdir -p "$(dirname "$CONF")"
    sed -i '/^[[:space:]]*capture[[:space:]]*=[[:space:]]*wlr[[:space:]]*$/d' "$CONF" 2>/dev/null || true
    if ! grep -Eq '^[[:space:]]*capture[[:space:]]*=' "$CONF" 2>/dev/null; then
        echo 'capture = portal' >> "$CONF"
    fi
    # The portal runs while the desktop session is up (firox-session.target)
    systemctl --user start firox-session.target 2>/dev/null || true
    # NVIDIA: Flatpak apps need the Flatpak build of the same driver version
    # for hardware encoding (NVENC)
    if [ -r /sys/module/nvidia/version ]; then
        nv="$(tr . - < /sys/module/nvidia/version)"
        echo "Installing NVIDIA's driver for Flatpak apps (version ${nv//-/.})..."
        flatpak install -y --or-update --user flathub "org.freedesktop.Platform.GL.nvidia-$nv" ||
            echo "Not on Flathub yet: Sunshine will encode without the GPU until it is (ujust update installs it then)."
    fi
    # (Re)start it now, with the settings above
    flatpak kill "$APP" 2>/dev/null || true
    sleep 1
    setsid -f flatpak run --command=sunshine "$APP" >/dev/null 2>&1
    sleep 4
    echo
    echo "Sunshine is running and will start whenever you log in."
    echo
    echo "1. Open https://localhost:47990 (opening it now). Your browser warns about"
    echo "   the certificate: that's expected, continue. Create a username and password."
    echo "2. Install Moonlight on the device you want to stream to and pick this computer."
    echo "3. Moonlight shows a PIN: type it in Sunshine's web page under PIN."
    xdg-open https://localhost:47990 >/dev/null 2>&1 || true
    echo
    read -rp "Press Enter to close" _

# Remove Sunshine, its input rules, autostart entry and firewall ports
[group('Firox Linux')]
remove-sunshine:
    #!/usr/bin/bash
    set -uo pipefail
    APP=dev.lizardbyte.app.Sunshine
    if ! flatpak info "$APP" >/dev/null 2>&1; then
        echo "Sunshine isn't installed."
        exit 0
    fi
    read -rp "Remove Sunshine and its settings? [y/N] " ans
    [[ "${ans,,}" == y* ]] || exit 0
    flatpak kill "$APP" 2>/dev/null
    flatpak run --command=remove-additional-install.sh "$APP"
    flatpak uninstall -y --delete-data "$APP"
    rm -f ~/.config/autostart/$APP.desktop
    zone="$(firewall-cmd --get-default-zone 2>/dev/null || echo FedoraWorkstation)"
    sudo firewall-cmd --permanent --zone="$zone" \
        --remove-port=47984/tcp --remove-port=47989/tcp --remove-port=48010/tcp \
        --remove-port=47998-48000/udp --remove-port=48002/udp --remove-port=48010/udp >/dev/null
    sudo firewall-cmd --reload >/dev/null
    echo "Sunshine removed."

# Firox Explorer is now Firox's file manager. This tidies up after the
# preview (`ujust install-explorer` used to add it as an add-on): it removes
# the old "Firox Explorer (preview)" Start menu entry and makes folders open
# in Firox Explorer again if you had chosen otherwise.
# Make Firox Explorer open folders again (and tidy up after the preview add-on)
[group('Firox Linux')]
install-explorer:
    #!/usr/bin/bash
    set -euo pipefail
    apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    if grep -qs 'Firox Explorer (preview)' "$apps/firox-explorer.desktop"; then
        rm -f "$apps/firox-explorer.desktop"
        update-desktop-database "$apps" 2>/dev/null || true
        echo "Removed the old preview Start menu entry."
    fi
    xdg-mime default firox-explorer.desktop inode/directory
    echo "Firox Explorer opens folders. It's in the Start menu as Firox Explorer."

# Throw away your desktop customisations and go back to the Firox Linux defaults
[group('Firox Linux')]
reset-desktop:
    #!/usr/bin/bash
    set -euo pipefail
    for d in labwc waybar mako fuzzel pcmanfm-qt/firox; do
        if [ -e ~/.config/$d ]; then
            mv ~/.config/$d ~/.config/$d.bak-$(date +%s)
            echo "Backed up ~/.config/$d"
        fi
    done
    echo "Done. Log off and back on to apply."

# Copy the default desktop config into ~/.config so you can edit it
[group('Firox Linux')]
customize-desktop:
    #!/usr/bin/bash
    set -euo pipefail
    for d in labwc; do
        if [ -e ~/.config/$d ]; then echo "~/.config/$d already exists, skipping"; continue; fi
        cp -r /etc/xdg/$d ~/.config/$d
        echo "Copied /etc/xdg/$d -> ~/.config/$d"
    done
    echo "Taskbar: add your own CSS rules to ~/.config/firox/panel.css (applied straight away)."
    echo "To use waybar instead, copy /etc/xdg/waybar to ~/.config/waybar and log off and back on."

# Switch the desktop look: "classic" (blue/green) or "modern" (grey, translucent)
[group('Firox Linux')]
theme NAME="":
    #!/usr/bin/bash
    if [ -z "{{ NAME }}" ]; then
        echo "Current theme: $(firox-theme)"
        echo "Available: $(firox-theme list | paste -sd ' ')"
        echo "Usage: ujust theme modern"
    else
        firox-theme set "{{ NAME }}"
    fi

# Light or dark apps, independent of the Classic/Modern look
# (auto = dark with Modern, light with Classic)
[group('Firox Linux')]
dark-mode MODE="":
    #!/usr/bin/bash
    case "{{ MODE }}" in
        "") echo "Apps: $(firox-theme apps)"; echo "Usage: ujust dark-mode on|off|auto" ;;
        on|dark) firox-theme apps dark ;;
        off|light) firox-theme apps light ;;
        auto) firox-theme apps auto ;;
        *) echo "Usage: ujust dark-mode on|off|auto" >&2; exit 2 ;;
    esac

# Monitor power saving and sleep timers (minutes, 0 = never), e.g.
#   ujust power-saving MONITOR_AC=10 SLEEP_BATTERY=20
# Same as Settings > Power Options
[group('Firox Linux')]
power-saving *SETTINGS:
    #!/usr/bin/bash
    if [ -z "{{ SETTINGS }}" ]; then
        firox-power
        echo "Usage: ujust power-saving KEY=MINUTES... (0 = never; ujust power-saving reset)"
    elif [ "{{ SETTINGS }}" = reset ]; then
        firox-power reset && firox-power
    else
        firox-power set {{ SETTINGS }} && firox-power
    fi

# Screen saver after idle: list them, or pick one ("none" = off) and the wait
#   ujust screensaver aquarium 5
# Same as Settings > Screen Saver
[group('Firox Linux')]
screensaver ID="" MINUTES="":
    #!/usr/bin/bash
    if [ -z "{{ ID }}" ]; then
        firox-screensaver
        echo "Available: none $(firox-screensaver list | cut -f1 | paste -sd ' ')"
        echo "Usage: ujust screensaver ID [MINUTES]   (preview: firox-screensaver preview ID)"
    else
        firox-screensaver set "{{ ID }}" {{ MINUTES }}
    fi

# Fix silent/crackling Realtek onboard audio with hello2himel/linux-audio-fix.
# Runs a pinned, checksum-verified copy; extra flags go to the script
# (e.g. --dry-run, --list-chips, --restore, --uninstall).
[group('Firox Linux')]
fix-realtek-audio *ARGS:
    #!/usr/bin/bash
    set -euo pipefail
    # Pinned to the version reviewed for Firox Linux. To update: change both
    # lines after reading the new script.
    COMMIT=4742f0b8153e863c3b800dd53b3fa255e89fed13
    SHA256=a378fb4e73d095a0da5a77305c00daf6934b8b31ff352f0729f28f6b94b1b0d7
    URL="https://raw.githubusercontent.com/hello2himel/linux-audio-fix/${COMMIT}/AudioFix.sh"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    echo "Downloading AudioFix (github.com/hello2himel/linux-audio-fix @ ${COMMIT:0:7})..."
    curl -fsSL "$URL" -o "$tmp/AudioFix.sh"
    if ! echo "${SHA256}  $tmp/AudioFix.sh" | sha256sum -c --quiet; then
        echo "Checksum mismatch: the downloaded script is not the reviewed version. Not running it." >&2
        exit 1
    fi
    args=({{ ARGS }})
    # On Firox Linux, keep the fix by replaying the chip commands at boot.
    # The script's default "modprobe" mode would force the legacy Intel
    # driver (dsp_driver=1, can break audio on newer laptops) and try to
    # rebuild the initramfs, which doesn't apply to image-based systems.
    case " ${args[*]:-} " in
        *" --persist-mode"*|*" --restore"*|*" --uninstall"*|*" --list-chips"*|*" --list "*|*" --no-persist "*) ;;
        *) args+=(--persist-mode systemd) ;;
    esac
    bash "$tmp/AudioFix.sh" "${args[@]}"
    echo
    echo "Firox Linux: ignore the /tmp path above. To undo later, use:"
    echo "  ujust fix-realtek-audio --restore     (put mixer settings back)"
    echo "  ujust fix-realtek-audio --uninstall   (stop re-applying at boot)"

# Switch to the Firox image with NVIDIA drivers (GTX 16xx / RTX 20xx and newer)
[group('Firox Linux')]
install-nvidia:
    #!/usr/bin/bash
    set -uo pipefail
    gpus="$(lspci -nn 2>/dev/null)"
    if ! grep -Eiq '(VGA compatible|3D|Display) controller.*NVIDIA' <<<"${gpus}"; then
        echo "No NVIDIA graphics card found, nothing to do."
        exit 1
    fi
    # Same check as Universal Blue: GK/GM/GP/GV chips (Kepler..Volta,
    # GTX 10-series and older) aren't supported by the open driver
    nv="$(grep -Ei '(VGA compatible|3D|Display) controller.*NVIDIA' <<<"${gpus}")"
    if ! grep -Pqv 'G(K|M|P|V)\d{3}' <<<"${nv}"; then
        echo "This NVIDIA card is older than the GTX 16 / RTX 20 series:"
        echo "  ${nv}"
        echo "Firox's NVIDIA image uses NVIDIA's open driver, which doesn't support it."
        exit 1
    fi
    if ! status="$(sudo bootc status --format json)"; then
        echo "Couldn't read the system status (bootc status)."
        exit 1
    fi
    # What updates follow (the spec), and what's running now
    current="$(jq -r '.spec.image.image // .status.booted.image.image.image // empty' <<<"${status}")"
    booted="$(jq -r '.status.booted.image.image.image // empty' <<<"${status}")"
    if [ -z "${current}" ]; then
        echo "Couldn't tell which Firox image this computer uses. bootc status says:"
        sudo bootc status
        exit 1
    fi
    case "${current}" in
        *-nvidia-open:*|*-nvidia-open)
            if [ "${booted}" != "${current}" ]; then
                echo "The switch to ${current} is already done: restart to use it."
            else
                echo "Already on the NVIDIA image (${current})."
            fi
            exit 0 ;;
    esac
    repo="${current%:*}"; tag="${current##*:}"
    [ "${repo}" = "${current}" ] && tag=latest
    target="${repo}-nvidia-open:${tag}"
    echo "This switches Firox to ${target},"
    echo "which includes NVIDIA's driver. Your files and settings are kept."
    echo "Android apps (Waydroid) will use software rendering on NVIDIA."
    read -rp "Continue? [y/N] " ans
    [[ "${ans,,}" == y* ]] || exit 0
    if ! sudo bootc switch "${target}"; then
        echo
        echo "The switch failed (see above). Check the internet connection and try again."
        exit 1
    fi
    echo
    if mokutil --sb-state 2>/dev/null | grep -q enabled; then
        echo "Secure Boot is on. Before restarting, run:  ujust enroll-secure-boot-key"
        echo "then pick 'Enroll MOK' in the blue screen at the next boot (password: universalblue)."
    fi
    echo "Restart to start using the NVIDIA driver:  systemctl reboot"

# Switch back from the NVIDIA image to the standard Firox image
[group('Firox Linux')]
remove-nvidia:
    #!/usr/bin/bash
    set -uo pipefail
    status="$(sudo bootc status --format json)" || exit 1
    current="$(jq -r '.spec.image.image // .status.booted.image.image.image // empty' <<<"${status}")"
    case "${current}" in
        *-nvidia-open:*) target="${current/-nvidia-open:/:}" ;;
        *) echo "Not on the NVIDIA image (${current}), nothing to do."; exit 0 ;;
    esac
    read -rp "Switch to ${target}? [y/N] " ans
    [[ "${ans,,}" == y* ]] || exit 0
    sudo bootc switch "${target}" || exit 1
    echo "Restart to finish:  systemctl reboot"
