# Firox Linux

**Website: [kurodenkou.github.io/firox](https://kurodenkou.github.io/firox/)**

A desktop Linux built on [Universal Blue](https://universal-blue.org/) (Fedora Atomic): image-based, self-updating, with a familiar classic desktop layout. It includes:

- **A custom Wayland desktop laid out like the classic XP desktop**: taskbar with a **firox** button, a two-column Start menu, Quick Launch, notification area, desktop icons and Settings (an XP-style Control Panel). It comes in three looks: **Modern** (grey and translucent, with dark apps; the default), **Classic** (blue "Luna"-style with a green start button) and **Glass** (frosted glass with bright edges over a colourful wallpaper).
- **Web apps as first-class apps** with Firefox + [PWAsForFirefox](https://pwasforfirefox.filips.si/) (`firefoxpwa`): install any site as a desktop app with its own window, taskbar button and Start menu entry.
- **Android apps** with [Waydroid](https://waydro.id/), integrated into the Start menu.

Firox Linux was called **FirefoxOS** in its first versions; see [Upgrading from FirefoxOS](#upgrading-from-firefoxos).

Everything (themes, icons, wallpaper, the Start menu) is original work. No Microsoft assets are used.

![Start menu](docs/screenshots/start-menu.png)

| Desktop | All Programs | Log Off |
| Lock screen | `firox-lock` (Super+L, Start menu, the idle timer and before sleep): looks like the login screen (the theme's `welcome.css`) with your account picture, the time and date and your wallpaper, a password box on every monitor, a Caps Lock warning and **Turn off computer** (Sleep, Turn Off, Restart). Uses the Wayland session-lock protocol through gtk-session-lock, so the screen stays locked even if the lock screen crashes; passwords are checked with PAM (`/etc/pam.d/firox-lock`). `firox-lock -f` returns once the screen is locked. swaylock is the fallback where session-lock isn't available |
|---|---|---|
| ![Desktop](docs/screenshots/desktop.png) | ![All Programs](docs/screenshots/all-programs.png) | ![Log Off dialog](docs/screenshots/log-off.png) |

### Classic, Modern or Glass

Same layout, three looks. **Modern** (the default) is grey, flat and translucent, with dark-mode apps. **Classic** is the blue and green one above:

| Modern Start menu | Modern Turn Off dialog |
|---|---|
| ![Modern Start menu](docs/screenshots/modern-start-menu.png) | ![Modern Turn off dialog](docs/screenshots/modern-turn-off.png) |

Modern also switches apps to dark mode: GTK3 and GTK4/libadwaita apps, Firefox and web apps (which follow the system colour scheme), and Qt apps like the file manager. Classic switches them back to light.

![Modern with dark apps](docs/screenshots/modern-dark-apps.png)

The login screen shows everyone's account picture and matches the default look:

| Modern | Classic |
|---|---|
| ![Modern login screen](docs/screenshots/login-modern.png) | ![Classic login screen](docs/screenshots/login-classic.png) |

**Glass** turns the taskbar, Start menu, pop-ups and window title bars into frosted glass: light, translucent, with bright rims, round glass window buttons and an original indigo-to-rose wallpaper ("Glass"). It's inspired by the [liquidglass](https://github.com/bergice/liquidglass) WebGL demo (MIT), which refracts and blurs a background image inside a rounded glass shape. Wayland desktops like this one can't blur what's behind a window, but nothing ever goes behind the taskbar except the wallpaper, so `firox-panel` paints that strip of wallpaper itself, blurred and slightly magnified at the top and bottom edges like the demo's lens, and the theme's frost and rims go on top. The Start menu and pop-ups are translucent sheets with the same rims. Apps stay light.

![Glass look](docs/screenshots/glass.png)

Want Modern with light apps, or Classic with dark ones? The Appearance window also has a **Programs** setting: **Match the look** (the default), **Light** or **Dark**. Light or Dark keeps apps that way whichever look you choose. From a terminal: `ujust dark-mode on|off|auto` (or `firox-theme apps dark|light|auto`).

Switch looks in **Settings → Appearance**, or with `ujust theme modern` / `ujust theme classic` / `ujust theme glass` (`firox-theme set NAME` does the same). The change applies to the running desktop immediately and is saved per user. The wallpaper switches too, unless you've picked your own. The default for users who haven't picked one is set in `/etc/firox/theme` (`modern`); change it to `classic` to make the XP look the default. On their first login, users get the default theme's wallpaper and light or dark app style.

<sub>Screenshots are from the built image running headless (`labwc` + `grim`).</sub>

## What's in the desktop

| Piece | What it is |
|---|---|
| Window manager | [labwc](https://labwc.github.io/) (wlroots, Openbox-style) with the `Firox-Luna` theme: blue gradient titlebars, rounded minimise/maximise/close buttons |
| Taskbar | `firox-panel` (GTK, layer-shell; window list over wlr-foreign-toplevel-management with pywayland): start button, Quick Launch, window buttons, notification area (app tray icons, keep the screen on, volume, battery, update, notifications), clock, on every monitor with that monitor's windows. **Windows of the same app share one button** ("3  Foot ▴"): click it to pick a window. Click a window button to show or minimise it; right-click for Restore / Minimize / Maximize / Close (or Minimize/Close group); middle-click closes. **Quick Launch** holds the programs you pin (right-click a program in the Start menu or a window button → **Pin to Quick Launch**; `firox-quicklaunch` from a terminal). Drag window buttons and Quick Launch icons to rearrange them. **Settings → Taskbar**: auto-hide, group similar windows, show Quick Launch, seconds on the clock (or right-click an empty part of the taskbar). Click the speaker for a volume slider with Mute and a choice of speakers/headphones (`firox-volume`). Click the battery (`firox-battery`) for charge and time left, battery health, a screen brightness slider and the power mode (Power saver / Balanced / Performance, via tuned-ppd); right-click it for Power Options. Styled by the theme's `panel.css`; your own rules go in `~/.config/firox/panel.css`. waybar is kept as a fallback (and runs instead if you set up `~/.config/waybar`). The taskbar and the desktop (pcmanfm-qt) run under `/usr/libexec/firox/keep-running`, which starts them again if they crash (pcmanfm-qt can when a monitor sleeps or wakes); restarts are logged in `~/.local/state/firox/session.log` |
| Start menu | `firox-startmenu`: Python + GTK3 + gtk-layer-shell. A **firox** button, user picture header, pinned **Internet** / **Web Apps** / **Android Apps**, most-used programs, **All Programs** (grouped, with Web Apps and Android Apps folders, type to search), Documents / Pictures / Music / Computer / Network Places, Settings, **Bazaar** (app store), Help, Search, Run, **Log Off** and **Turn Off Computer** dialogs |
| Desktop | `firox-desktop` (GTK, layer-shell background on every monitor): the wallpaper on each monitor, the icons (Computer, home folder, Network, Recycle Bin and the files in `~/Desktop`) on the main display. Double-click opens; programs (AppImages, binaries) run, asking once before marking a download executable, and scripts ask Run or Open (if one quits at once with an error, its output is shown; logs in `~/.local/state/firox/programs/`); drag icons to move them (remembered in `~/.config/firox/desktop-icons.json`), onto a folder or the Recycle Bin, or out to other programs; drop files on it to copy them (Shift moves). Right-click: Open, Open With, Cut, Copy, Rename, Move to Recycle Bin, Properties; on the desktop Arrange Icons By, Refresh, Paste, New Folder / Text Document. Keys: Delete, Shift+Delete, F2, F5, Enter, Ctrl+A/C/X/V. **Settings → Desktop Background**: wallpaper, position (Fill, Fit, Stretch, Center, Tile), colour and which icons show. Settings are in `~/.config/pcmanfm-qt/firox/settings.conf` (shared with firox-theme and the Glass taskbar; `firox_wallpaper.py`). pcmanfm-qt's desktop is the fallback if firox-desktop can't run |
| Settings | `firox-control-panel` (the Control Panel): Display, Sound, Network, Bluetooth, Printers, Disks, Web Apps, Android, Bazaar, Date and Time, **Taskbar**, **Default Programs** (web browser, email, files, music, videos, pictures, text and PDF), **User Accounts** (change your account picture from a built-in set or any image, or your password; also opened by clicking the picture in the Start menu), System... |
| Web Apps | `firox-webapps`: list, open and remove installed PWAs |
| Login | `firox-welcome`, a greetd greeter in cage (`firox-greeter`): "To begin, click your user name" with everyone's account picture, Other user… for accounts not listed, and Turn off computer (sleep, turn off, restart). Styled to match the default look in `/etc/firox/theme`. Pictures come from AccountsService (`/var/lib/AccountsService/icons`), which Settings → User Accounts updates. Uses the keyboard layout chosen when installing. Falls back to gtkgreet if it can't start |
| Displays | **Settings → Display** shows the monitors as numbered screens: pick the **main display** (full taskbar with tray and clock, the Sidebar and notifications; the others extend the desktop with a taskbar of their own windows, the start button and the clock), **Identify** shows the numbers on the monitors. The choice is kept per set of monitors in `~/.config/firox/main-display` (`firox-displays main [NAME|NUMBER]`; `firox_monitors.py`). Settings → Taskbar → **Show the taskbar on all displays** off: one taskbar, on the main display. **Resolution and arrangement…** (`firox-display`) opens wdisplays and saves the layout when you close it (`firox-displays save`: a kanshi profile per set of connected monitors; resolutions the screen doesn't advertise are saved as custom modes). At login kanshi applies it and `firox-displays restore` double-checks and re-applies anything that didn't take. Log: `~/.local/state/firox/displays.log` |
| Mouse and keyboard | **Settings → Mouse**: pointer speed and acceleration, left-handed buttons, double-click speed, pointer size, scroll speed, natural scrolling, and a Touchpad tab (tap to click, two-finger or edge scrolling, right-click method, ignore while typing, off when a mouse is plugged in). **Settings → Keyboard**: repeat delay and rate, Num Lock at login, keyboard layouts with variants and a switching shortcut. Stored by `firox-input` in `~/.config/firox/input.conf`; it writes complete labwc config files to `~/.local/share/firox/xdg/labwc`, which labwc reads after `~/.config/labwc` (so a personal rc.xml still wins). The layout chosen when installing is the default |
| Notifications | mako pop-ups, plus a bell in the notification area (`firox-notifications`) that opens a panel with the last 100 notifications (click one to open its app). **Do Not Disturb** (in the panel, or right-click the bell) holds pop-ups except urgent ones. Pop-ups are also held while a screen saver runs |
| Volume and brightness keys | `firox-osd`: the volume, mute, microphone-mute and brightness keys show a small level popup at the bottom of the screen (also with Do Not Disturb on, and on the lock screen). Volume up unmutes; brightness never goes fully black |
| Updates | Updates download in the background and apply at the next restart. When one is waiting, an icon appears in the notification area (`firox-updates`): click it to restart, right-click to see which packages change. You also get a notification once per update, and "Firox Linux was updated" after restarting |
| Snipping Tool | `firox-snip` (Start → All Programs, or **Super+Shift+S** to go straight to selecting): snip a rectangle, the whole screen or one monitor, with an optional 3/5/10 second delay. Each snip is copied to the clipboard; mark it up with a pen or highlighter, then Copy, Save (to Pictures/Screenshots) or Save As (PNG or JPEG). **Print Screen** still saves the whole screen straight to Pictures, and **Shift+Print Screen** saves an area there |
| Calendar | Click the clock for the time, date and a month calendar (`firox-calendar`), with a link to Date and Time settings; right-click the clock to open those settings directly |
| Clipboard | `firox-clipboard` (cliphist): what you copy stays available after the app you copied it from closes (normally it's lost on Wayland), **Super+V** clipboard history with search and images, never stores items password managers mark as sensitive, cleared at logout by default. **Settings → Clipboard** sets the history size or turns it off. The Command Prompt also takes `Ctrl+Insert` / `Shift+Insert`, and Waydroid shares the clipboard with Android (`python3-pyclip`) |
| Power saving | **Settings → Power Options** (`firox-power`): on laptops, low-battery warnings (10%, then urgently at 5%) and at 3% the computer goes to sleep (or hibernates or shuts down, as you choose) after a 15-second warning. Also: turn off the monitor, lock the screen and put the computer to sleep after a set idle time, with separate **Plugged in** and **On battery** times on laptops. Defaults: lock after 10 min; monitor off after 15 min (5 on battery); sleep never (30 min on battery). Videos and full-screen apps keep the screen on. System defaults: `/etc/firox/power.conf` |
| Screen savers | **Settings → Screen Saver** (`firox-screensaver`): Aquarium (asciiquarium), Matrix (cmatrix), Bonsai (cbonsai), Bouncing firox, Starfield or Blank, full screen on every monitor after a set idle time (off by default), with Preview. [Add your own](#adding-screen-savers) |
| Desktop Gadgets | `firox-gadgets`: a Vista-style Sidebar on the right of the desktop (layer-shell, behind windows) with Clock, Calendar, CPU Meter, Weather (Open-Meteo, no account; set your city in its options), Slide Show (your Pictures folder), Notes, **Now Playing** (MPRIS: title, artist, cover art, ⏮ ⏯ ⏭; click elsewhere to raise the player), **Network Meter** (speeds and a graph from /proc/net/dev), **Feed Headlines** (any RSS/Atom feed, 4 per page, click to open; BBC World News by default), **Timer** (notification and sound when it's done), **Picture Puzzle** (4×4 sliding tiles from your Pictures or a Firox wallpaper), **Currency** (ECB rates via frankfurter.app, no account) and **Disk Space** (home and plugged-in drives). **+ Gadgets** opens the gallery; hover a gadget for ×, right-click for Options, Move up/down, Close, Hide. On at login; turn it off in Settings → Taskbar. Settings: `~/.config/firox/gadgets.conf` |
| Task Manager | `firox-taskmgr` (Ctrl+Alt+Del, Ctrl+Shift+Esc): **Applications** (open windows: Switch To, End Task), **Processes** (CPU and memory per process, sortable, all users optional, End Process) and **Performance** (green-on-black CPU and memory history graphs, totals, physical memory, swap). btop is in the Start menu as Resource Monitor |
| System Restore | `firox-restore` (Settings → System Restore): lists the installed versions of Firox Linux (`rpm-ostree status`) and restores the previous one (`rpm-ostree rollback`, with Undo), keeps a version past later updates (`ostree admin pin`), shows what changed between versions, cancels a waiting update, and pauses automatic updates so a restored version stays. Files, settings and Flatpaks aren't affected |
| Backup and Restore | `firox-backup` (Settings → Backup and Restore): backs up your home folder every hour to a USB drive or a network folder (smb://…), and as soon as the backup drive is plugged in. Each backup is a dated folder with a complete copy, unchanged files hard-linked to the previous one (rsync `--link-dest`, like the original Time Machine); hourly kept for a day, daily for a month, then weekly, oldest removed when the drive is full. Drives without hard links (FAT/exFAT, most shares) get a Latest copy plus Older versions. Restore window: pick a backup by date, pick files, Restore (or Restore everything). Backups are plain folders you can open anywhere |
| Sync | `firox-sync` (Settings → Sync): Firox computers on the same network find each other with Avahi (Bonjour, `_firox-sync._tcp`), pair with a 6-digit PIN shown on the other computer (HMAC proof over both IDs; the PIN never crosses the network), and keep the folders you tick (Documents, Pictures, Music, Videos, Desktop) the same on all of them. The engine is a private Syncthing instance, local network only (no global discovery or relays); replaced or deleted files are kept 30 days in `.stversions` |
| Run | `firox-run` (Start → Run…, Super+R): an XP-style Run box. Type a program (command or Start menu name), a folder, a file, a web address or any shell command; the rest of a matching command, program or path fills in as you type. Remembers the last 26 (the arrow lists them, the last one is filled in), Browse… picks a file, "Run in a terminal window" shows a command's output, and terminal programs (btop, nano, ssh…) open in one automatically. Old Windows names work: `cmd`, `notepad`, `calc`, `control`, `taskmgr`, `explorer`, `msconfig`, `iexplore`, `winver`, `ncpa.cpl`… |
| Wi-Fi and network | `firox-network`: click the network icon on the taskbar (signal bars, wired, connecting, off). Wi-Fi on/off switch; networks in range, strongest first with the current one at the top; click one to connect (password box inline, wrong passwords aren't kept), disconnect or forget it. Also your wired connection, VPNs (on/off), Airplane mode (rfkill), **Hidden network…** and **Network Connections…** (nm-connection-editor) for networks that need a username or certificate. Uses NetworkManager's libnm; right-click the icon for Network Connections |
| Bluetooth | `firox-bluetooth`: click the Bluetooth icon (shown when the computer has Bluetooth). On/off switch; your devices, connected first, with battery level when they report it; click one to connect, disconnect or remove it. **Add a device…** looks for devices nearby; click one and Pair (it's trusted and connected straight away). Its pairing helper (a BlueZ agent started at login) asks you to compare a code, type a PIN or allow a new device, in a small window. **Bluetooth settings…** opens Blueman for file sending and audio profiles |
| Startup Programs | `firox-startup` (Settings → Startup Programs, or `msconfig` in Run): tick what starts at login. Add any Start menu program or your own command; untick to pause one without removing it. Entries are standard `~/.config/autostart/*.desktop` files, so apps' own "start at login" options appear here too, and they start a few seconds after login once the notification area is up (`~/.local/state/firox/startup.log` says what started). Desktop Gadgets can be turned off here, and the classic nm-applet and Blueman tray icons turned on (off by default) |
| Extras | `zenity` for dialogs from shell scripts (`zenity --question`, `--info`, `--file-selection`, `--progress`…), mako "balloon" notifications, xfce4-terminal "Command Prompt" (tabs, Edit > Preferences; Super+Enter), btop "Task Manager", VLC "Media Player" (music, videos, DVDs; full codecs from the base image) |

![Taskbar: a group of three terminals opened, the same windows ungrouped, and the Modern look](docs/screenshots/taskbar.png)

![Battery popup, Classic and Modern](docs/screenshots/battery.png)

![Desktop Gadgets: the Sidebar and Add Gadgets](docs/screenshots/gadgets.png)

![Task Manager: Processes and Performance](docs/screenshots/task-manager.png)

![System Restore](docs/screenshots/system-restore.png)

![Wi-Fi and Bluetooth pop-ups (Classic and Modern) and the pairing prompt](docs/screenshots/network-bluetooth.png)

![Run, Startup Programs and Add a Startup Program](docs/screenshots/run-startup.png)

![Backup and Restore](docs/screenshots/backup.png)

![Sync: the Sync window and the PIN on the other computer](docs/screenshots/sync.png)

![Boot splash](docs/screenshots/boot-splash.png)

![Power Options on a laptop](docs/screenshots/power-options.png)

![Clipboard history (Super+V)](docs/screenshots/clipboard-history.png)

![Notification panel, Modern and Classic](docs/screenshots/notifications.png)

![Volume and brightness popups, Modern and Classic](docs/screenshots/osd.png)

| Volume (Modern, Classic) | Alt+Tab |
|---|---|
| ![Volume popup](docs/screenshots/volume.png) | ![Alt+Tab](docs/screenshots/alt-tab.png) |

| Snipping Tool | Calendar (Modern, Classic) |
|---|---|
| ![Snipping Tool](docs/screenshots/snipping-tool.png) | ![Calendar](docs/screenshots/calendar.png) |

| Mouse → Touchpad | Keyboard → Layouts |
|---|---|
| ![Mouse settings](docs/screenshots/mouse-touchpad.png) | ![Keyboard settings](docs/screenshots/keyboard-layouts.png) |

### Keyboard shortcuts

`Super` Start menu · `Super+E` Computer · `Super+R` Run · `Super+D` show desktop · `Super+L` lock · `Super+V` clipboard history · `Super+←/→` snap · `Alt+Tab` switch windows (with live previews) · `Alt+Space` window menu · `Ctrl+Alt+Del` Task Manager · `PrtSc` screenshot · `Super+Shift+S` Snipping Tool

## Repository layout

```
Containerfile                 FROM ghcr.io/ublue-os/base-main, runs build.sh
build_files/build.sh          repos, packages, services, branding
system_files/                 copied onto / in the image
  etc/xdg/labwc/              compositor config, keybinds, autostart
  etc/xdg/waybar/             fallback taskbar (firox-panel is the default)
  etc/greetd/                 login screen
  etc/firefox/policies/       auto-installs the PWAsForFirefox extension
  usr/bin/firox-*         start menu, control panel, web apps, helpers
  usr/share/themes/Firox-Luna/   window theme + SVG buttons
  usr/share/ublue-os/just/60-custom.just   `ujust` recipes
disk_config/                  qcow2 / ISO definitions for bootc-image-builder
scripts/nightly-build.sh      nightly build for your own machine (see below)
Justfile                      local build helpers (from ublue image-template)
```

## Installing

### Rebase an existing Fedora Atomic / Universal Blue system

```bash
sudo bootc switch ghcr.io/kurodenkou/firox:latest
```

```bash
systemctl reboot
```

### Fresh install from an ISO

Build one locally (this is the usual way), or run **Actions → Build disk images** (`build-disk.yml`, manual only) in GitHub and download the `anaconda-iso` artifact:

```bash
just build-iso ghcr.io/kurodenkou/firox latest
```

The installer creates your user account. There's no separate setup wizard.

### NVIDIA graphics

There's a second image, **`ghcr.io/kurodenkou/firox-nvidia-open`**, with NVIDIA's driver built in. It uses Universal Blue's prebuilt, signed open kernel modules, matched to the image's kernel at build time. It supports GTX 16-series / RTX 20-series and newer cards.

- **On an installed system:** run `ujust install-nvidia`. It checks your card, switches to the NVIDIA image and keeps your files and settings. `ujust remove-nvidia` switches back.
- **Secure Boot:** run `ujust enroll-secure-boot-key` before restarting, then choose **Enroll MOK** at the next boot (password `universalblue`).
- **Fresh install:** build the ISO from the NVIDIA image: `just build-nvidia`, then `just build-iso localhost/firox-nvidia-open`. The installed system then updates from the NVIDIA image: each image records its own update address in `/usr/share/firox/image-ref`, and the installer points updates there. (ISOs built before this fix pointed every install at the standard image, so the first update dropped the NVIDIA driver; `ujust install-nvidia` switches back.)
- **Safety net:** if the computer has a supported NVIDIA card but runs the standard image, a notification at login (once) offers to switch.
- **Waydroid** uses software rendering on NVIDIA (`ujust setup-waydroid` configures that automatically). NVIDIA's driver doesn't support Waydroid's GPU path.

### Try it in a VM

```bash
just build
```

```bash
just build-qcow2
```

```bash
just run-vm-qcow2
```

The qcow2 image has a test user `xp` with password `firox` (set in `disk_config/disk.toml`).

### Upgrading from FirefoxOS

FirefoxOS machines track the old image, `ghcr.io/kurodenkou/firefoxos`, which no longer gets updates. Switch each one to the new image once, then restart:

```bash
sudo bootc switch ghcr.io/kurodenkou/firox:latest
```

From then on it updates normally (`ujust update`). On your first login afterwards, your theme choice, Start menu history and desktop settings move to their new `firox` locations automatically. The old `firefoxos-*` commands still work as aliases for `firox-*`.

## After first login

- **Web apps:** the Firefox runtime for web apps downloads in the background on first login. Open a site in Firefox, click the PWAsForFirefox toolbar button and choose **Install**.
- **Android:** open **Start → Android Apps**. The first time, it runs `ujust setup-waydroid` to download Android. Use `ujust setup-waydroid gapps` for the Play Store.
- **More apps:** **Start → Bazaar** opens the Bazaar Flatpak app store (the first time, it offers to install it).

### `ujust` recipes

| Recipe | Does |
|---|---|
| `ujust setup-waydroid [vanilla\|gapps]` | Download and initialise Android |
| `ujust waydroid-google-id` | Print the Android ID to register a GAPPS install |
| `ujust reset-waydroid` | Delete Android and its data |
| `ujust fix-realtek-audio` | Fix silent/crackling Realtek onboard audio ([linux-audio-fix](https://github.com/hello2himel/linux-audio-fix), pinned + checksum-verified; also in Settings → Sound Troubleshooter) |
| `ujust install-webapp-runtime` | (Re)install the firefoxpwa Firefox runtime |
| `ujust install-app-store` | Install the Bazaar Flatpak store |
| `ujust customize-desktop` | Copy the labwc defaults into `~/.config` to edit |
| `ujust reset-desktop` | Back up your overrides and restore defaults |
| `ujust theme [classic\|modern\|glass]` | Switch between the Classic, Modern and Glass looks |
| `ujust screensaver [ID] [MINUTES]` | List screen savers, or pick one (`none` turns them off) and how long to wait |
| `ujust dark-mode [on\|off\|auto]` | Dark or light apps regardless of the look (`auto` follows it) |
| `ujust power-saving [KEY=MINUTES...\|reset]` | Show or change the monitor-off, lock and sleep timers (e.g. `MONITOR_AC=10 SLEEP_BATTERY=20`; 0 = never) |
| `ujust install-nvidia` / `ujust remove-nvidia` | Switch to / from the image with NVIDIA drivers |
| `ujust install-sunshine` / `ujust remove-sunshine` | Install [Sunshine](https://app.lizardbyte.dev/Sunshine/) (Flathub) to stream games and the desktop to [Moonlight](https://moonlight-stream.org/) on a phone, tablet, TV or PC: sets up its virtual input devices, opens its streaming ports, starts it at login (Startup Programs) and opens its web page at `https://localhost:47990` |

## Customising

### Adding screen savers

![Screen savers](docs/screenshots/screensavers.png)

Any program that runs in a terminal can be a screen saver. Create `~/.local/share/firox/screensavers/NAME.conf` (or `/etc/firox/screensavers/` for every user):

```ini
NAME=Pipes
COMMAND=pipes.sh
```

It shows up in **Settings → Screen Saver** straight away. It runs full screen in a black terminal and stops when you move the mouse or press a key. For a program that opens its own window instead, add `TERMINAL=false`. `COMMAND` can be a script you drop in `~/.local/bin` (handy for single-file savers such as `pipes.sh`) or any other program on your `PATH`. The built-in ones live in `/usr/share/firox/screensavers/`. The default for new users is in `/etc/firox/screensaver` (`none`).

### Everything else

Each look's styling lives in `/usr/share/firox/themes/{classic,modern,glass}/` (taskbar CSS, Start menu CSS, notifications, clipboard picker, lock screen), with window themes in `/usr/share/themes/Firox-{Luna,Modern,Glass}`. To add another look, copy one of those folders and edit it; it shows up in Settings → Appearance automatically (`GLASS=1` in its `theme.conf` gives it the frosted-wallpaper taskbar). System defaults live in `/etc/xdg/{labwc,waybar,mako,foot,fuzzel}`. Anything you put in the matching `~/.config/` folder overrides them. The Start menu's pinned and default apps are at the top of `system_files/usr/bin/firox-startmenu` (`PINNED`, `DEFAULT_MFU`). The menu also takes `--all-programs`, `--power` and `--log-off`, which you can bind to keys in `rc.xml`.

## Building and signing

Images (`ghcr.io/<owner>/firox` and `ghcr.io/<owner>/firox-nvidia-open`) are built and published by `scripts/nightly-build.sh` on a home lab build box (below). The GitHub Actions workflows don't run on their own: not on pushes, pull requests or a schedule. Start `build.yml` by hand (**Actions → Build container image → Run workflow**) if the build box is out of action. The website in `docs/` is served by GitHub Pages (Settings → Pages: deploy from the `main` branch, `/docs` folder). Disk images (`build-disk.yml`) are only built when started by hand. To sign images, generate a key pair with `cosign generate-key-pair` and add the private key as the `SIGNING_SECRET` repository secret. Without it, the build still runs and signing is skipped.

### Building nightly on your own machine

`scripts/nightly-build.sh` does what the GitHub build does, on a build box in your home lab, using the same Justfile recipes. It updates its own checkout of `main`, builds the standard and NVIDIA images, rechunks, tags, pushes (to ghcr.io and/or a registry on your network) and signs them. It can also build installer ISOs.

- **Needs:** `git`, `just`, `podman`, `jq` and `python3` (plus `cosign` to sign). Allow about 50 GB of disk. ISOs need root: run the script as root, or give its user passwordless `sudo`.
- **Settings:** copy `scripts/nightly-build.conf.example` to `~/.config/firox-build.conf`. To push to ghcr.io, put a [token with `write:packages`](https://github.com/settings/tokens/new?scopes=write:packages) in `~/.config/firox-ghcr-token` (`chmod 600`).
- **Nightly with cron** (`crontab -e`):

  ```
  MAILTO=you@example.com
  30 2 * * *  $HOME/firox/scripts/nightly-build.sh
  ```

- **Results:** a log per run in `~/.local/state/firox-build/` (kept 14 days), and a short summary on output. It exits non-zero if anything failed, so cron mails you. A night that finds the previous build still running skips itself.
- **Options:** `--variant main|nvidia`, `--no-push`, `--dry-run`.
- **Testing a pull request:** `scripts/nightly-build.sh --pr 42` (or `--branch NAME`) builds it without publishing anything or making ISOs, so you can check it builds before merging.

## Known limitations

- Waydroid needs a GPU it supports for hardware acceleration (Intel/AMD are fine; on NVIDIA it runs with software rendering) and doesn't work on Secure Boot setups that block the binder module from loading.
- The NVIDIA image only supports cards from the GTX 16 / RTX 20 series onwards (NVIDIA's open driver).
- The PWAsForFirefox extension is installed by Firefox policy on first launch, so Firefox needs network access the first time.
- The Start menu, Settings and Web Apps manager are new code and haven't been tested much on real hardware yet.
