# OrangeFox device tree - Infinix X6885 (MT6789)

Recovery-in-`vendor_boot` tree generated from the stock `vendor_boot.img`
(Android 16, build `BP2A.250605.031.A3`, `X6885-16.3.0.145`).

| | |
|---|---|
| Device | Infinix X6885 (`Infinix-X6885`, board `x6885_h8923`) |
| SoC | MediaTek MT6789 (Helio G99 family), arm64 only |
| Kernel | GKI 6.12.38-android16, 4K pages (lives in `boot`, not touched) |
| Layout | A/B + Virtual A/B, dynamic partitions (`super`), no `recovery` partition |
| vendor_boot | header v4, 64 MiB, platform + "recovery" ramdisk fragments, DT table, bootconfig |
| Display | 1080x2400, density 420, `BGRA_8888` |
| Encryption | FBE `aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized` + metadata encryption |

## Build with carlodandan/OrangeFox-Action-Builder

Push the **contents of this folder** to the root of your own GitHub repo, then run
the *OrangeFox - Build* workflow with:

| Input | Value |
|---|---|
| MANIFEST_BRANCH | `12.1` (**not** 11.0: boot header v4 needs 12.1) |
| DEVICE_TREE | URL of your repo |
| DEVICE_TREE_BRANCH | your branch (e.g. `main`) |
| DEVICE_PATH | `device/infinix/X6885` (must match `DEVICE_PATH` in BoardConfig.mk) |
| DEVICE_NAME | `X6885` |
| BUILD_TARGET | `vendorboot` |

The builder runs `lunch twrp_X6885-eng && mka adbd vendorbootimage`.
Output: `out/target/product/X6885/OrangeFox*.img`.

Before the first build set `OF_MAINTAINER` in `vendorsetup.sh` (OrangeFox variables are exported there, using fox_12.1 names).

## Flash (read all of it first)

**Do NOT patch/flash vbmeta with `--disable-verity --disable-verification` and do NOT flash the raw
image produced by the build.Transsion firmware ("P7 anti-crack") answers unsigned full images / patched vbmeta with a red "Unauthorized Repair" screen and a deliberate soft-brick. This is a single-source report and not verified here, but the downside
is a soft-brick, so the safe path below only replaces the *recovery* ramdisk fragment of the STOCK image.

The stock `vendor_boot` has two ramdisk fragments: *platform* (used for normal Android boot, ~29.7 MB) and
*recovery*. `tools/make_vendor_boot.py` keeps everything of the stock image (header, platform fragment, DTB,
bootconfig, 64 MiB size, AVB footer + vbmeta blob) and swaps only the recovery fragment:

```
python3 tools/make_vendor_boot.py stock_vendor_boot.img ramdisk-recovery.img vendor_boot_fox.img
fastboot flash vendor_boot vendor_boot_fox.img
fastboot reboot recovery
```
`ramdisk-recovery.img` is the OrangeFox ramdisk from the build (`out/target/product/X6885/`). The tool also
accepts the build's `vendor_boot.img` as input and uses its recovery fragment.
It refuses ramdisks that do not look like a recovery or that do not fit (partition is 64 MiB; the recovery
fragment can be at most about 37.2 MB). If the build's fragment is too big, files that are byte-identical to the stock
platform fragment are dropped and the fragment is re-compressed with lz4 -12 (same format as the build).
**Never use gzip for the recovery fragment**: a gzip fragment was tested on a real X6885 and the recovery showed a black
screen and looped (normal boot was fine).

Always keep the stock `vendor_boot.img` and `boot.img`. If anything goes wrong, flash the stock `vendor_boot.img` back.
