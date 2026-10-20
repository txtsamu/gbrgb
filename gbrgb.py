#!/usr/bin/env python3
"""gbrgb - tiny Gigabyte RGB Fusion 2 (ITE IT5701/IT5702, USB 048d:5702) controller for Linux.

Packet layout taken from Gigabyte Control Center's RgbMotherboard module
(MCU_8297.cs) and cross-checked against OpenRGB's GigabyteRGBFusion2USBController.
Talks to /dev/hidrawN directly; needs the OpenRGB udev rules (uaccess) or root.
Stop OpenRGB before using it.
"""
import argparse
import fcntl
import glob
import json
import os
import struct
import sys
import time

REPORT_ID = 0xCC
VID, PID = 0x048D, 0x5702

EFFECT_STATIC = 1
EFFECT_PULSE = 2
EFFECT_BLINK = 3
EFFECT_CYCLE = 4
EFFECT_RANDOM = 8
MODES = ("static", "breathing", "flashing", "doubleflash", "cycle", "random")
ZONE_NAMES = {  # hardware zone index -> header (from Gigabyte's code: TurnOff_DStrip uses 5/6)
    0: "zone0 (I/O?)", 1: "zone1", 2: "zone2", 3: "zone3 (PCIe?)", 4: "zone4",
    5: "D_LED1?", 6: "D_LED2?", 7: "zone7", 8: "zone8", 9: "zone9", 10: "zone10",
}


def hidiocsfeature(n):
    return (3 << 30) | (n << 16) | (ord("H") << 8) | 0x06


def hidiocgfeature(n):
    return (3 << 30) | (n << 16) | (ord("H") << 8) | 0x07


def find_hidraw():
    for sys_dev in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(sys_dev, "device", "uevent")) as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("HID_ID="):
                _, vid, pid = line.split("=")[1].split(":")
                if int(vid, 16) == VID and int(pid, 16) == PID:
                    return "/dev/" + os.path.basename(sys_dev)
    sys.exit("no 048d:5702 hidraw device found")


class Fusion2:
    def __init__(self, path=None, verbose=False):
        self.path = path or find_hidraw()
        self.fd = os.open(self.path, os.O_RDWR)
        self.verbose = verbose

    def send(self, *payload):
        data = bytes([REPORT_ID]) + bytes(payload)
        data += bytes(64 - len(data))
        if self.verbose:
            print("  >", data[:16].hex(" "))
        fcntl.ioctl(self.fd, hidiocsfeature(64), data)

    def info(self):
        self.send(0x60, 0x00)
        buf = bytearray(64)
        buf[0] = REPORT_ID
        fcntl.ioctl(self.fd, hidiocgfeature(64), buf)
        return bytes(buf)

    # -- commands (opcode names from MCU_8297.cs) ----------------------------------
    def apply(self, mask=0xFF, mask_hi=0x00):          # Apply(): CC 28 <mask>
        self.send(0x28, mask & 0xFF, mask_hi & 0xFF)

    def strip_ctrl(self, bits):                          # Enable_DLedStripCtrl(): CC 32 <bits>
        self.send(0x32, bits)

    def keep_lighting(self, enable):                     # SaveLedParameter(): CC 47 <0/1>
        self.send(0x47, 1 if enable else 0)

    def led_count(self, *tiers):                         # SetDLedOutputCount(): CC 34
        t = list(tiers) + [0] * (6 - len(tiers))
        self.send(0x34, (t[1] << 4) | t[0], (t[3] << 4) | t[2], (t[5] << 4) | t[4])

    def effect(self, zone, mode, rgb, brightness=100, p0=0, p1=0, p2=0, p3=0, params=(0, 0, 0, 0)):
        """write_to_mcu(): CC <0x20+zone | 0x90+zone-8> then DataFormat_8297."""
        hdr = 0x20 + zone if zone < 8 else 0x90 + (zone - 8)
        r, g, b = rgb
        color0 = (r << 16) | (g << 8) | b                # OpenRGB: RGBToBGRColor -> 0x00RRGGBB
        body = struct.pack("<IIBBBBIIHHHHBBBB",
                           1 << zone, 0, 0, mode, brightness, 0,
                           color0, 0, p0, p1, p2, p3, *params)
        self.send(hdr, *body)

    def reset(self):                                      # ResetController()
        for z in range(8):
            self.send(0x20 + z, 0, 0)
        self.apply(0xFF)

    def mode(self, zone, name, rgb, speed=4, brightness=100):
        """Hardware effects. Timings/params are the ones OpenRGB's driver sends (verified on this board)."""
        if name == "static":
            return self.static(zone, rgb, brightness)
        s = max(0, min(9, speed))
        params = (0, 0, 0, 0)
        p0 = p1 = p2 = 0
        if name == "breathing":
            etype = EFFECT_PULSE
            p0 = 400 + s * 100 if s <= 6 else 1000 + (s - 6) * 200
            p1, p2 = p0, 200
        elif name in ("flashing", "doubleflash"):
            etype = EFFECT_BLINK
            p0, p1, p2 = 100, 100, s * 200 + 700
            if name == "doubleflash":
                params = (0, 1, 2, 0)
        elif name == "cycle":
            etype = EFFECT_CYCLE
            p0 = (s * 100 + 300) + (1300 * (s - 8) if s > 8 else 0)
            p1 = p0 - 200
            params = (7, 0, 0, 0)
        elif name == "random":
            etype = EFFECT_RANDOM
            p0 = 100
            params = (1, 5, 0, 0)
        else:
            raise ValueError("unknown mode %r" % name)
        self.effect(zone, etype, rgb, brightness, p0, p1, p2, 0, params)
        self.apply(1 << zone if zone < 8 else 0xFF, (1 << (zone - 8)) if zone >= 8 else 0)

    def static(self, zone, rgb, brightness=100):
        self.effect(zone, EFFECT_STATIC, rgb, brightness)
        self.apply(1 << zone if zone < 8 else 0xFF, (1 << (zone - 8)) if zone >= 8 else 0)


def parse_color(s):
    s = s.lstrip("#")
    names = {"red": "ff0000", "green": "00ff00", "blue": "0000ff", "white": "ffffff",
             "purple": "ff00ff", "yellow": "ffff00", "cyan": "00ffff", "off": "000000"}
    s = names.get(s.lower(), s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def cmd_info(dev, a):
    raw = dev.info()
    print("hidraw        :", dev.path)
    print("raw reply     :", raw[:48].hex(" "))
    print("product       :", raw[12:40].split(b"\0")[0].decode(errors="replace"))
    print("fw version    : %d.%d.%d.%d" % tuple(raw[4:8]))
    print("strip counts  : argb01=0x%02x argb23=0x%02x argb45=0x%02x" % (raw[8], raw[9], raw[10]))
    print("support flags : 0x%02x  (bit0 = keep-lighting 0x47, bit1 = 0x48)" % raw[11])


def init(dev):
    dev.info()
    dev.reset()
    dev.send(0x31, 0)          # EnableBeat(false)
    dev.strip_ctrl(0)          # all built-in effects enabled on the D_LED headers
    dev.led_count(0, 0, 0, 0, 0, 0)


def cmd_sweep(dev, a):
    init(dev)
    print("Each zone lights RED for %ds. Tell me the zone number when your FAN turns red.\n" % a.seconds)
    for z in range(11):
        print("  >>> zone %-2d  (%s)" % (z, ZONE_NAMES.get(z, "")))
        dev.static(z, (255, 0, 0))
        time.sleep(a.seconds)
        dev.static(z, (0, 0, 0))
    print("\nsweep done")


def cmd_set(dev, a):
    init(dev)
    zones = range(11) if a.zone == "all" else [int(z) for z in a.zone.split(",")]
    for z in zones:
        dev.static(z, parse_color(a.color))
    keep = (not a.no_keep) and (dev.info()[11] & 1)
    if keep:
        dev.keep_lighting(True)
    print("set zone(s) %s -> %s (keep-lighting cmd %s)" % (list(zones), a.color, "sent" if keep else "not supported by firmware, skipped"))


CONFIG = "/etc/gbrgb.json"


def cmd_config(dev_unused, a):
    cfg = {"zones": {str(z): a.color for z in a.zones.split(",")}}
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print("wrote", CONFIG, cfg)


def saved_color(zone, default="ff00ff"):
    try:
        with open(CONFIG) as f:
            return json.load(f)["zones"].get(str(zone), default)
    except (OSError, KeyError, ValueError):
        return default


def cmd_mode(dev, a):
    init(dev)
    color = a.color or saved_color(a.zone)
    dev.mode(int(a.zone), a.name, parse_color(color), a.speed)
    print("zone %s -> %s (%s, speed %d)" % (a.zone, a.name, color, a.speed))


def cmd_apply(dev, a):
    with open(a.config) as f:
        cfg = json.load(f)
    init(dev)
    for z, color in cfg["zones"].items():
        dev.static(int(z), parse_color(color))
    print("applied", cfg["zones"])


def open_with_retry(path=None, verbose=False, tries=30):
    for i in range(tries):
        try:
            return Fusion2(path, verbose)
        except (SystemExit, OSError):
            time.sleep(1)
    sys.exit("controller not available")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    s = sub.add_parser("sweep")
    s.add_argument("-s", "--seconds", type=float, default=4)
    s.set_defaults(fn=cmd_sweep)
    s = sub.add_parser("set")
    s.add_argument("zone", help="zone index, comma list, or 'all'")
    s.add_argument("color", help="name or RRGGBB")
    s.add_argument("--no-keep", action="store_true")
    s.set_defaults(fn=cmd_set)
    s = sub.add_parser("mode", help="hardware effect: " + ", ".join(MODES))
    s.add_argument("zone")
    s.add_argument("name", choices=MODES)
    s.add_argument("--color", help="name or RRGGBB (default: the saved colour)")
    s.add_argument("--speed", type=int, default=4, help="0 (slow) .. 9 (fast), default 4")
    s.set_defaults(fn=cmd_mode)
    s = sub.add_parser("config", help="write %s (needs root)" % CONFIG)
    s.add_argument("zones", help="zone index or comma list, e.g. 6")
    s.add_argument("color")
    s.set_defaults(fn=cmd_config)
    s = sub.add_parser("apply", help="apply the saved config (used by the systemd units)")
    s.add_argument("--config", default=CONFIG)
    s.set_defaults(fn=cmd_apply)
    a = p.parse_args()
    dev = None if a.cmd == "config" else open_with_retry(verbose=a.verbose)
    a.fn(dev, a)


if __name__ == "__main__":
    main()
