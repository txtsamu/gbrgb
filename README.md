# gbrgb

Tiny Linux controller for Gigabyte RGB Fusion 2 lighting (ITE IT5701/IT5702, USB `048d:5702`). Talks to `/dev/hidrawN` directly with the packets Gigabyte Control Center sends (reverse-engineered from its `RgbMotherboard` module). Written for a B650M K whose ARGB fan header is hardware zone 6 (D_LED2).

```
gbrgb info                         # read the controller (firmware, support flags)
gbrgb sweep                        # light each hardware zone red in turn to find your header
gbrgb set 6 ff00ff                 # solid colour on zone 6 (name or RRGGBB)
gbrgb mode 6 breathing --color ff0000 --speed 4
                                   # modes: static breathing flashing doubleflash cycle random
gbrgb config 6 ff00ff              # write /etc/gbrgb.json (needs root)
gbrgb apply                        # apply the saved config (used by the systemd units)
```

The colour is held by the controller after the process exits. Do not run OpenRGB at the same time: its start-up resets the controller. Needs read/write access to the hidraw device (OpenRGB's udev rules grant it). `systemd/` has units that re-apply the saved colour at boot and after sleep. Full write-up: claude-research `gigabyte-b650m-k-argb-fan-no-light-openrgb-gbrgb.md`.
