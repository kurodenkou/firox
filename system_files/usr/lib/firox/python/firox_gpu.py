"""GPU usage for Firox Task Manager (the GPU tab and the Processes tab's GPU
column), without root and without extra daemons.

    gpus = firox_gpu.find()          # [Gpu], one per graphics card
    sampler = firox_gpu.Sampler()    # call sample() every second or two
    snap = sampler.sample()          # Snapshot: per-GPU stats, per-process use

Where the numbers come from:
- AMD (amdgpu): sysfs: gpu_busy_percent, mem_info_vram_*, hwmon temperature,
  power, fan; the current clock from pp_dpm_sclk.
- NVIDIA: nvidia-smi (usage, memory, temperature, power, clock, fan; per
  process from `nvidia-smi pmon` and --query-compute-apps).
- Intel (i915, xe) and everything else: no "busy" file exists, so usage is
  added up from the DRM usage stats the kernel keeps for each program
  (/proc/PID/fdinfo: drm-engine-* nanoseconds, or drm-cycles-* for xe), the
  way nvtop does it. That counts your own programs (including the desktop
  itself), which is what's using an integrated GPU on a desktop computer.
- Per-process GPU % and GPU memory: the same fdinfo stats, for every driver
  that has them (amdgpu, i915, xe, nouveau, msm, panfrost, v3d…).

SYS and PROC can be pointed elsewhere for tests.
"""

import os
import re
import shutil
import subprocess
import time

SYS = "/sys"
PROC = "/proc"
PCI_IDS = ("/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids")
VENDORS = {"0x1002": "AMD", "0x8086": "Intel", "0x10de": "NVIDIA"}


def _read(path, default=None):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def _int(path):
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


_PCI_NAMES = {}


def pci_name(vendor, device):
    """'Navi 23 [Radeon RX 6600]' from pci.ids, or None."""
    key = (vendor.lower().replace("0x", ""), device.lower().replace("0x", ""))
    if key in _PCI_NAMES:
        return _PCI_NAMES[key]
    name = None
    for path in PCI_IDS:
        try:
            with open(path, errors="replace") as fh:
                in_vendor = False
                for line in fh:
                    if not line.strip() or line.startswith("#"):
                        continue
                    if not line.startswith("\t"):
                        if in_vendor:
                            break
                        in_vendor = line[:4].lower() == key[0]
                    elif in_vendor and not line.startswith("\t\t") and line[1:5].lower() == key[1]:
                        name = line[5:].strip()
                        break
        except OSError:
            continue
        if name:
            break
    _PCI_NAMES[key] = name
    return name


class Gpu:
    """One graphics card (/sys/class/drm/cardN)."""

    def __init__(self, card):
        self.card = card                                   # "card0"
        self.dev = os.path.join(SYS, "class", "drm", card, "device")
        self.driver = os.path.basename(os.path.realpath(os.path.join(self.dev, "driver"))) or "?"
        self.vendor_id = _read(os.path.join(self.dev, "vendor"), "")
        self.device_id = _read(os.path.join(self.dev, "device"), "")
        self.vendor = VENDORS.get(self.vendor_id, self.driver)
        self.pci = os.path.basename(os.path.realpath(self.dev))   # 0000:03:00.0
        model = pci_name(self.vendor_id, self.device_id) if self.vendor_id else None
        self.name = f"{self.vendor} {model}" if model else f"{self.vendor} graphics ({self.driver})"
        self.hwmon = None
        try:
            for h in sorted(os.listdir(os.path.join(self.dev, "hwmon"))):
                self.hwmon = os.path.join(self.dev, "hwmon", h)
                break
        except OSError:
            pass
        # integrated GPUs have no memory of their own
        self.vram_total = _int(os.path.join(self.dev, "mem_info_vram_total"))
        self.integrated = self.vendor == "Intel" and not self.vram_total

    def __repr__(self):
        return f"<Gpu {self.card} {self.name} ({self.driver}, {self.pci})>"

    def read_sysfs(self):
        """What the driver tells us directly. Missing values are None."""
        d = self.dev
        out = {"busy": _int(os.path.join(d, "gpu_busy_percent")),
               "vram_used": _int(os.path.join(d, "mem_info_vram_used")),
               "vram_total": _int(os.path.join(d, "mem_info_vram_total")),
               "temp": None, "power": None, "clock": None, "fan": None}
        if self.hwmon:
            t = _int(os.path.join(self.hwmon, "temp1_input"))
            out["temp"] = t / 1000 if t is not None else None
            for f in ("power1_average", "power1_input"):
                p = _int(os.path.join(self.hwmon, f))
                if p is not None:
                    out["power"] = p / 1e6
                    break
            pwm, pwm_max = _int(os.path.join(self.hwmon, "pwm1")), _int(os.path.join(self.hwmon, "pwm1_max")) or 255
            if pwm is not None:
                out["fan"] = 100 * pwm / pwm_max
        sclk = _read(os.path.join(d, "pp_dpm_sclk"))
        if sclk:
            for line in sclk.splitlines():
                if line.rstrip().endswith("*"):
                    m = re.search(r"(\d+)\s*Mhz", line, re.I)
                    if m:
                        out["clock"] = int(m.group(1))
        if out["clock"] is None:
            card = os.path.join(SYS, "class", "drm", self.card)
            for f in ("gt_act_freq_mhz", "gt/gt0/rps_act_freq_mhz", "device/tile0/gt0/freq0/act_freq"):
                v = _int(os.path.join(card, f))
                if v:
                    out["clock"] = v
                    break
        return out


def find():
    """The graphics cards, primary first."""
    base = os.path.join(SYS, "class", "drm")
    try:
        cards = sorted((c for c in os.listdir(base) if re.fullmatch(r"card\d+", c)),
                       key=lambda c: int(c[4:]))
    except OSError:
        return []
    out, seen = [], set()
    for c in cards:
        g = Gpu(c)
        if g.pci in seen or not os.path.exists(os.path.join(g.dev, "vendor")):
            continue                     # a second node of the same card, or not a PCI/platform GPU
        seen.add(g.pci)
        out.append(g)
    return out


# --------------------------------------------------------------- fdinfo --

def parse_fdinfo(text):
    """The DRM usage stats in one /proc/PID/fdinfo/N, or None if it isn't a
    DRM client. {'pdev', 'client', 'engines': {name: ns}, 'cycles': {name:
    (cycles, total)}, 'capacity': {name: n}, 'memory': bytes}."""
    if "drm-driver:" not in text:
        return None
    info = {"pdev": None, "client": None, "engines": {}, "cycles": {}, "capacity": {}, "memory": 0}
    totals = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "drm-pdev":
            info["pdev"] = value
        elif key == "drm-client-id":
            info["client"] = value
        elif key.startswith("drm-engine-capacity-"):
            try:
                info["capacity"][key[len("drm-engine-capacity-"):]] = int(value)
            except ValueError:
                pass
        elif key.startswith("drm-engine-"):
            try:
                info["engines"][key[len("drm-engine-"):]] = int(value.split()[0])
            except (ValueError, IndexError):
                pass
        elif key.startswith("drm-total-cycles-"):
            totals[key[len("drm-total-cycles-"):]] = int(value.split()[0])
        elif key.startswith("drm-cycles-"):
            info["cycles"][key[len("drm-cycles-"):]] = int(value.split()[0])
        elif key.startswith(("drm-resident-", "drm-memory-")):
            # memory this program has on the GPU (all regions, e.g. vram, gtt, system)
            m = re.match(r"(\d+)\s*(KiB|MiB|GiB)?", value)
            if m:
                n = int(m.group(1)) * {"KiB": 1024, "MiB": 1 << 20, "GiB": 1 << 30, None: 1}[m.group(2)]
                if key.startswith("drm-resident-") or "drm-resident-" not in text:
                    info["memory"] += n
    for name, c in info["cycles"].items():
        info["cycles"][name] = (c, totals.get(name))
    return info


def drm_clients(pids=None):
    """{(pid, pdev, client-id): info} for the processes we can look at."""
    out = {}
    if pids is None:
        try:
            pids = [int(p) for p in os.listdir(PROC) if p.isdigit()]
        except OSError:
            return out
    for pid in pids:
        fd_dir = os.path.join(PROC, str(pid), "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue                        # gone, or not ours
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if not target.startswith("/dev/dri/"):
                continue
            text = _read(os.path.join(PROC, str(pid), "fdinfo", fd))
            info = parse_fdinfo(text) if text else None
            if info is None:
                continue
            key = (pid, info["pdev"], info["client"] or f"fd{fd}")
            out.setdefault(key, info)       # one client can have several fds
    return out


# --------------------------------------------------------------- nvidia --

def nvidia_smi():
    """{pci: stats} and {(pci, pid): (percent, bytes)} from nvidia-smi, or
    ({}, {}) when there's no NVIDIA driver."""
    if shutil.which("nvidia-smi") is None:
        return {}, {}
    gpus, procs = {}, {}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=pci.bus_id,utilization.gpu,memory.used,memory.total,"
                            "temperature.gpu,power.draw,clocks.gr,fan.speed,index",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}, {}
    index_pci = {}

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None
    for line in r.stdout.splitlines():
        f = [x.strip() for x in line.split(",")]
        if len(f) < 9:
            continue
        pci = f[0].lower()
        if pci.count(":") == 2 and len(pci.split(":")[0]) == 8:
            pci = pci[4:]                   # 00000000:01:00.0 -> 0000:01:00.0
        used, total = num(f[2]), num(f[3])
        gpus[pci] = {"busy": num(f[1]), "vram_used": used * (1 << 20) if used is not None else None,
                     "vram_total": total * (1 << 20) if total is not None else None, "temp": num(f[4]),
                     "power": num(f[5]), "clock": num(f[6]), "fan": num(f[7])}
        index_pci[f[8]] = pci
    try:
        r = subprocess.run(["nvidia-smi", "pmon", "-c", "1", "-s", "um"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            if line.startswith("#"):
                continue
            f = line.split()
            if len(f) < 5 or not f[1].isdigit():
                continue
            pci = index_pci.get(f[0])
            sm = num(f[3]) if f[3] != "-" else 0
            procs[(pci, int(f[1]))] = [sm or 0, 0]
        r = subprocess.run(["nvidia-smi", "--query-compute-apps=gpu_bus_id,pid,used_memory",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.splitlines():
            f = [x.strip() for x in line.split(",")]
            if len(f) == 3 and f[1].isdigit():
                pci = f[0].lower()
                pci = pci[4:] if pci.count(":") == 2 and len(pci.split(":")[0]) == 8 else pci
                entry = procs.setdefault((pci, int(f[1])), [0, 0])
                entry[1] = (num(f[2]) or 0) * (1 << 20)
    except (OSError, subprocess.SubprocessError):
        pass
    return gpus, {k: tuple(v) for k, v in procs.items()}


# -------------------------------------------------------------- sampler --

class Snapshot:
    def __init__(self):
        self.gpus = {}        # pci -> {'busy', 'vram_used', 'vram_total', 'shared', 'temp', 'power', 'clock', 'fan'}
        self.procs = {}       # pid -> {pci: (percent, bytes)}


class Sampler:
    """Usage between one sample() and the next (the first sample has no
    usage figures yet, just memory)."""

    def __init__(self):
        self.gpus = find()
        self.last = None      # (time_ns, {key: info})

    def sample(self, pids=None):
        snap = Snapshot()
        now = time.monotonic_ns()
        clients = drm_clients(pids)
        nv_gpus, nv_procs = nvidia_smi() if any(g.vendor == "NVIDIA" for g in self.gpus) else ({}, {})
        prev_t, prev = self.last if self.last else (None, {})
        self.last = (now, clients)
        engine_sum = {}       # (pci, engine) -> busy fraction added up over clients
        for key, info in clients.items():
            pid, pci = key[0], info["pdev"]
            if pci is None and len(self.gpus) == 1:
                pci = self.gpus[0].pci
            pct = 0.0
            old = prev.get(key)
            if old is not None and prev_t is not None and now > prev_t:
                dt = now - prev_t
                for eng, ns in info["engines"].items():
                    d = ns - old["engines"].get(eng, ns)
                    if d > 0:
                        frac = d / dt / max(1, info["capacity"].get(eng, 1))
                        engine_sum[(pci, eng)] = engine_sum.get((pci, eng), 0) + frac
                        pct = max(pct, frac * 100)
                for eng, (c, total) in info["cycles"].items():
                    oc, ot = old["cycles"].get(eng, (c, total))
                    if total is not None and ot is not None and total > ot and c >= oc:
                        frac = (c - oc) / (total - ot)
                        engine_sum[(pci, eng)] = engine_sum.get((pci, eng), 0) + frac
                        pct = max(pct, frac * 100)
            use = snap.procs.setdefault(pid, {})
            p0, m0 = use.get(pci, (0.0, 0))
            use[pci] = (min(100.0, max(p0, pct)), m0 + info["memory"])
        for (pci, pid), (pct, mem) in nv_procs.items():
            use = snap.procs.setdefault(pid, {})
            p0, m0 = use.get(pci, (0.0, 0))
            use[pci] = (max(p0, pct), max(m0, mem))
        for g in self.gpus:
            st = nv_gpus.get(g.pci) if g.vendor == "NVIDIA" and nv_gpus else g.read_sysfs()
            st = dict(st or {})
            if st.get("busy") is None and prev_t is not None:
                busiest = [v for (pci, _e), v in engine_sum.items() if pci == g.pci]
                st["busy"] = min(100.0, 100 * max(busiest)) if busiest else 0.0
            # memory programs have on this GPU (all of it, for integrated graphics)
            st["shared"] = sum(m for use in snap.procs.values() for pci, (_p, m) in use.items() if pci == g.pci)
            snap.gpus[g.pci] = st
        return snap
