#!/system/bin/sh
# Writes diagnostic snapshots of the running recovery to /mnt/vendor/persist/fox_debug/ so they
# can be read from Android afterwards (recovery has no working touch/adb on this device yet).
# Read-only probes; only text files are written. Delete the folder when done.
D=${FOX_DEBUG_DIR:-/mnt/vendor/persist/fox_debug}
S1=${FOX_DEBUG_SLEEP1:-20}
S2=${FOX_DEBUG_SLEEP2:-40}
# wait (max 30 s) for persist to be mounted
i=0
while [ $i -lt 30 ]; do
  if [ -n "$FOX_DEBUG_DIR" ] || grep -q " /mnt/vendor/persist " /proc/mounts 2>/dev/null; then break; fi
  sleep 1; i=$((i+1))
done
mkdir -p "$D" 2>/dev/null
snap() {
  O="$D/snap_$1.txt"
  {
    echo "== label $1 | date $(date) | uptime $(cat /proc/uptime)"
    echo "== cmdline"; cat /proc/cmdline
    echo "== selinux: $(getenforce 2>&1)"
    echo "== props"; getprop | grep -E "ro.debuggable|ro.secure|ro.adb|sys.usb|ro.hardware|init.svc|trustonic|ro.boot.(slot|force|mode|hardware)|twrp|orangefox" 
    echo "== loaded modules"; awk '{print $1}' /proc/modules | tr '\n' ' '; echo
    echo "== input devices"; cat /proc/bus/input/devices 2>&1; ls -l /dev/input 2>&1
    echo "== spi devices / drivers"; ls /sys/bus/spi/devices /sys/bus/spi/drivers 2>&1
    echo "== usb: udc"; ls /sys/class/udc 2>&1
    for u in /sys/class/udc/*; do echo "$u state: $(cat $u/state 2>&1)"; done
    echo "cmode: $(cat /sys/class/udc/musb-hdrc/device/cmode 2>&1)"
    echo "== usb: gadget"; ls /config/usb_gadget/g1 2>&1; echo "UDC=[$(cat /config/usb_gadget/g1/UDC 2>&1)]"
    ls -l /config/usb_gadget/g1/configs/b.1 2>&1; ls -l /dev/usb-ffs/adb 2>&1
    echo "== processes"; ps -A 2>&1 | grep -E "adbd|mobicore|tee-service|keymint|gatekeeper|recovery|ueventd|fox_" 
    echo "== mounts"; head -60 /proc/mounts
    echo "== bcb log"; cat /tmp/fox_bcb.log 2>&1
    echo "== recovery.log (tail)"; tail -n 150 /tmp/recovery.log 2>&1
    echo "== dmesg (filtered, tail 400)"; dmesg 2>&1 | grep -i -E "fts|gt9896|goodix|adaptive|tran_touch|tpd|touch|spi1|musb|usb|udc|gadget|mobicore|trustonic|tee|firmware|avc:|denied|insmod|module|probe|defer|lcm" | tail -n 400
    echo "== dmesg (last 120 lines)"; dmesg 2>&1 | tail -n 120
  } > "$O" 2>&1
  sync
}
sleep $S1; snap 1
sleep $S2; snap 2
exit 0
