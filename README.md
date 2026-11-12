# Firox Linux

**[kurodenkou.github.io/firox](https://kurodenkou.github.io/firox/)**: screenshots, features, installing and the guide all live on the website.

A desktop Linux built on [Universal Blue](https://universal-blue.org/) (Fedora Atomic): image-based, self-updating, with a familiar classic desktop.

- **A Wayland desktop laid out like the classic XP desktop:** a taskbar with a **firox** button, a two-column Start menu, Quick Launch, desktop icons, its own file manager and Settings. It comes in three looks: [Modern, Classic and Glass](https://kurodenkou.github.io/firox/looks.html).
- **Web apps as real apps,** with Firefox and [PWAsForFirefox](https://pwasforfirefox.filips.si/).
- **Android apps** with [Waydroid](https://waydro.id/).
- **Updates that can't break your computer:** each update is a complete new version of the system, switched to when you restart, and System Restore takes you back if one misbehaves.

Themes, icons, wallpapers and the Start menu are original work; no Microsoft assets are used. It was briefly called FirefoxOS, until it was determined that the name was already used, and was quickly renamed Firox Linux.

![Firox Linux desktop with the Start menu open](docs/screenshots/start-menu.png)

## Install

On Fedora Atomic or Universal Blue, switch to Firox and restart:

```bash
sudo bootc switch ghcr.io/kurodenkou/firox:latest
```

```bash
systemctl reboot
```

For a fresh install from an ISO, NVIDIA graphics (`ghcr.io/kurodenkou/firox-nvidia-open`), or trying it in a VM, see **[Install](https://kurodenkou.github.io/firox/install.html)**.

## Documentation

| | |
|---|---|
| [Features](https://kurodenkou.github.io/firox/features.html) | Everything in the desktop, with screenshots, and the keyboard shortcuts |
| [Looks](https://kurodenkou.github.io/firox/looks.html) | Modern, Classic and Glass, light or dark apps |
| [Install](https://kurodenkou.github.io/firox/install.html) | Installing, NVIDIA, VMs, and your first login |
| [Guide](https://kurodenkou.github.io/firox/guide.html) | `ujust` commands, customising, building and signing, nightly builds, known limitations |
| [Under the hood](https://kurodenkou.github.io/firox/internals.html) | What each part of the desktop is and how it works |

The help that comes with Firox (Start → Help and Support) is in `system_files/usr/share/firox/help/`.

## Building

```bash
just build
```

```bash
just build-iso
```

`just build` makes the image; `just build-iso` makes an installer from it. Published images are built nightly by `scripts/nightly-build.sh` on a home build machine; the GitHub Actions workflows only run when started by hand. The website is `docs/`, served by GitHub Pages. Details: [Building and signing](https://kurodenkou.github.io/firox/guide.html#building) and the [repository layout](https://kurodenkou.github.io/firox/guide.html#layout).

[Report a problem](https://github.com/kurodenkou/firox/issues) · Licensed under the [Apache License 2.0](LICENSE)
