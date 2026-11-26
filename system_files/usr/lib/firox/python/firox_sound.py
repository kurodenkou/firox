"""What sound hardware a Firox computer has, what's wrong with it, and the
fixes that fit (the Sound Troubleshooter, firox-sound).

Three kinds of onboard sound need different fixes:
  - HD-Audio with a Realtek codec (most desktops and older laptops): the
    chip may need its startup commands (ujust fix-realtek-audio).
  - USB audio built into the motherboard (Realtek ALC4080/ALC4082/ALC1220-VB
    USB on newer boards): WirePlumber's UCM profile for it can route sound to
    the wrong jack, and USB power saving can make it drop out.
  - Intel laptops with SOF (Sound Open Firmware): sound needs firmware and a
    "topology" for the laptop; new models can lack one, and some laptops'
    speakers sit behind separate amplifiers (Cirrus CS35L41, TI TAS2781).
Everything here only reads; the fixes are in firox-sound and, for the two
that need root, /usr/libexec/firox/sound-admin.
"""

import glob
import json
import os
import re
import subprocess

ROOT = os.environ.get("FIROX_SOUND_ROOT", "")            # tests point this at a fake /proc and /sys
WP_CONF = os.path.expanduser("~/.config/wireplumber/wireplumber.conf.d/51-firox-usb-audio.conf")
UDEV_RULE = "/etc/udev/rules.d/90-firox-usb-audio.rules"
LEGACY_KARG = "snd_intel_dspcfg.dsp_driver=1"
# Motherboard makers whose boards carry Realtek USB audio (USB-Audio.conf in alsa-ucm)
BOARD_VENDORS = {"0db0": "MSI", "0b05": "ASUS", "0414": "Gigabyte", "26ce": "ASRock"}
AMPS = {"CSC3551": "Cirrus Logic CS35L41", "TIAS2781": "Texas Instruments TAS2781",
        "TXNW2781": "Texas Instruments TAS2781"}


def read(path):
    try:
        with open(ROOT + path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def run(argv, timeout=10):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def cards():
    """[{index, id, driver, name, kind, codec, usbid, maker, label}] from /proc/asound."""
    out = []
    text = read("/proc/asound/cards")
    for m in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(\S+)\s+-\s+(.+)$", text, re.M):
        idx, cid, driver, name = int(m.group(1)), m.group(2), m.group(3), m.group(4).strip()
        card = {"index": idx, "id": cid, "driver": driver, "name": name, "codec": "", "usbid": "", "maker": ""}
        codecs = sorted(glob.glob(f"{ROOT}/proc/asound/card{idx}/codec#*"))
        for c in codecs:
            first = re.search(r"^Codec:\s*(.+)$", read(c[len(ROOT):]), re.M)
            if first and "HDMI" not in first.group(1) and "Display" not in first.group(1):
                card["codec"] = first.group(1).strip()
                break
        card["usbid"] = read(f"/proc/asound/card{idx}/usbid").strip().lower()
        sysdrv = os.path.basename(os.path.realpath(f"{ROOT}/sys/class/sound/card{idx}/device/driver")) \
            if os.path.exists(f"{ROOT}/sys/class/sound/card{idx}/device/driver") else ""
        hdmi_only = not card["codec"] and codecs and driver == "HDA-Intel"
        if driver == "USB-Audio":
            vendor = card["usbid"].split(":")[0]
            card["maker"] = BOARD_VENDORS.get(vendor, "")
            card["kind"] = "usb-board" if card["maker"] else "usb"
        elif "sof" in driver.lower() or "sof" in cid.lower() or sysdrv.startswith("sof-"):
            card["kind"] = "sof"
        elif driver == "HDA-Intel" and hdmi_only:
            card["kind"] = "hdmi"
        elif driver == "HDA-Intel":
            card["kind"] = "hda-realtek" if card["codec"].startswith("Realtek") else "hda"
        else:
            card["kind"] = "other"
        card["label"] = label(card)
        out.append(card)
    return out


def label(card):
    k = card["kind"]
    if k == "hda-realtek":
        return f"{card['codec']} (onboard, HD-Audio)"
    if k == "hda":
        return f"{card['codec'] or card['name']} (onboard, HD-Audio)"
    if k == "usb-board":
        return f"{card['maker']} motherboard USB audio (Realtek ALC4080 or similar), {card['usbid']}"
    if k == "usb":
        return f"{card['name']} (USB)"
    if k == "sof":
        return f"Intel laptop audio with SOF firmware ({card['name']})"
    if k == "hdmi":
        return f"{card['name']} (HDMI / DisplayPort only)"
    return card["name"]


def amplifiers():
    """Separate speaker amplifiers some laptops use: [name]."""
    found = []
    for hid_path in glob.glob(f"{ROOT}/sys/bus/acpi/devices/*/hid"):
        hid = read(hid_path[len(ROOT):]).strip()
        if hid in AMPS and AMPS[hid] not in found:
            found.append(AMPS[hid])
    return found


def outputs():
    """[{name, description, muted, volume, card, ports, port}] and the default output's name."""
    rc, out, _e = run(["pactl", "-f", "json", "list", "sinks"])
    sinks = []
    if rc == 0:
        try:
            for s in json.loads(out):
                vols = [v.get("value_percent", "0%").rstrip("%") for v in (s.get("volume") or {}).values()]
                sinks.append({"name": s.get("name", ""), "description": s.get("description", s.get("name", "")),
                              "muted": bool(s.get("mute")),
                              "volume": max((int(v) for v in vols if v.isdigit()), default=0),
                              "card": (s.get("properties") or {}).get("alsa.card", ""),
                              "ports": [(p.get("name"), p.get("description"), p.get("availability"))
                                        for p in s.get("ports") or []],
                              "port": s.get("active_port")})
        except (ValueError, AttributeError):
            pass
    rc, default, _e = run(["pactl", "get-default-sink"])
    return sinks, default.strip() if rc == 0 else ""


def pulse_cards():
    """{alsa card index: card name as WirePlumber knows it (device.name)}."""
    rc, out, _e = run(["pactl", "-f", "json", "list", "cards"])
    names = {}
    if rc == 0:
        try:
            for c in json.loads(out):
                idx = (c.get("properties") or {}).get("alsa.card")
                if idx is not None:
                    names[str(idx)] = c.get("name", "")
        except (ValueError, AttributeError):
            pass
    return names


def kernel_messages():
    """This boot's kernel lines about sound (journalctl; wheel can read it)."""
    rc, out, _e = run(["journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat",
                       "-g", r"sof|snd_|snd-|hda|ALSA|usb.*[Aa]udio|cs35l41|tas2781"], timeout=20)
    return out.strip().splitlines()[-200:] if rc == 0 else []


def sof_trouble(lines):
    """Kernel lines that say SOF couldn't start: firmware or topology missing."""
    bad = re.compile(r"sof.*(fail|error|not found|unable|missing)|(topology|firmware).*(fail|not found|missing)",
                     re.I)
    return [ln for ln in lines if bad.search(ln)]


def legacy_driver_on():
    return LEGACY_KARG in read("/proc/cmdline")


def usb_power_rule():
    return os.path.exists(ROOT + UDEV_RULE)


def plain_profiles_on():
    return os.path.exists(WP_CONF)


def findings():
    """Everything the troubleshooter shows: {cards, amps, outputs, default, kernel, problems}."""
    cs = cards()
    sinks, default = outputs()
    kernel = kernel_messages()
    problems = []
    real = [s for s in sinks if s["name"] != "auto_null"]
    if not cs:
        problems.append("No sound hardware was found. Check that onboard audio is turned on in the computer's "
                        "BIOS/UEFI settings, and that headphones or speakers are plugged into this computer.")
    elif not real:
        problems.append("Firox sees the sound hardware, but there's no output to play through (only \"Dummy "
                        "Output\"). That usually means the driver or its firmware couldn't start.")
    d = next((s for s in real if s["name"] == default), None)
    if d is not None:
        if d["muted"]:
            problems.append(f"The output in use, {d['description']}, is muted.")
        elif d["volume"] < 10:
            problems.append(f"The output in use, {d['description']}, is at {d['volume']}% volume.")
        if "hdmi" in d["name"].lower() and any("hdmi" not in s["name"].lower() for s in real):
            problems.append(f"Sound goes to {d['description']} (a monitor or TV). If you expected speakers or "
                            "headphones, pick them below.")
        if d["port"] and any(p[0] == d["port"] and p[2] == "not available" for p in d["ports"]):
            problems.append(f"{d['description']} plays through a jack with nothing plugged in.")
    if any(c["kind"] == "sof" for c in cs) and sof_trouble(kernel):
        problems.append("The kernel reports that Intel's SOF sound firmware couldn't start on this laptop.")
    return {"cards": cs, "amps": amplifiers(), "outputs": sinks, "default": default, "kernel": kernel,
            "problems": problems}


def report(f=None):
    """Plain text to paste when asking for help (no personal details)."""
    f = f or findings()
    lines = ["Firox Sound Troubleshooter report", ""]
    os_release = read("/etc/os-release")
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)', os_release, re.M)
    lines.append(f"System: {m.group(1) if m else 'Firox'}; kernel {os.uname().release}")
    driver = f"legacy Intel HD-Audio driver ({LEGACY_KARG})" if legacy_driver_on() else "default"
    lines.append(f"Kernel options: {driver}")
    lines += ["", "Sound hardware:"]
    for c in f["cards"]:
        lines.append(f"  card {c['index']} [{c['id']}] {c['driver']}: {c['label']}")
    if not f["cards"]:
        lines.append("  (none)")
    if f["amps"]:
        lines.append("Speaker amplifiers: " + ", ".join(f["amps"]))
    lines += ["", "Outputs:"]
    for s in f["outputs"]:
        mark = "*" if s["name"] == f["default"] else " "
        lines.append(f" {mark} {s['description']} ({s['name']}) volume {s['volume']}%{' MUTED' if s['muted'] else ''}"
                     + (f", port {s['port']}" if s["port"] else ""))
    if not f["outputs"]:
        lines.append("  (none: PipeWire has nothing to play through)")
    lines += ["", "Fixes in place: " + (", ".join(n for n, on in (
        ("USB audio plain profiles", plain_profiles_on()), ("USB audio power saving off", usb_power_rule()),
        ("legacy Intel driver", legacy_driver_on())) if on) or "none")]
    if f["problems"]:
        lines += ["", "Found:"] + [f"  - {p}" for p in f["problems"]]
    lines += ["", "Kernel messages about sound (this boot):"] + [f"  {ln}" for ln in f["kernel"][-60:]]
    return "\n".join(lines) + "\n"
