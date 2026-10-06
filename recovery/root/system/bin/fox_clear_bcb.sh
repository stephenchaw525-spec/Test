#!/system/bin/sh
# If the bootloader message in misc still says "boot-recovery", clear ONLY its 32-byte command
# field (what the stock recovery does when it exits). Without this, a forced power-off inside
# the recovery makes every next boot return to recovery. Nothing else in misc is touched.
MISC=${FOX_MISC:-/dev/block/by-name/misc}
LOG=${FOX_BCB_LOG:-/tmp/fox_bcb.log}
{
  echo "misc=$MISC"
  if [ ! -e "$MISC" ]; then echo "no misc device"; exit 0; fi
  CMD=$(dd if="$MISC" bs=32 count=1 2>/dev/null | tr -d '\000')
  echo "command=[$CMD]"
  if [ "$CMD" = "boot-recovery" ]; then
    CONV=""; [ -f "$MISC" ] && CONV="conv=notrunc"
    dd if=/dev/zero of="$MISC" bs=32 count=1 $CONV 2>/dev/null
    sync
    echo "cleared command field"
  else
    echo "nothing to clear"
  fi
} > "$LOG" 2>&1
exit 0
