#!/usr/bin/env python3
"""
make_vendor_boot.py - build a flashable vendor_boot.img by swapping ONLY the "recovery"
ramdisk fragment of the STOCK vendor_boot.img. Everything else stays byte-identical:
header, platform fragment (normal Android boot), DTB, bootconfig, partition size and the
stock AVB footer/vbmeta blob (relocated if the content grew).

    python3 tools/make_vendor_boot.py STOCK_vendor_boot.img NEW_RAMDISK OUT.img [--fit auto|none]

NEW_RAMDISK is either
  * a raw lz4 ramdisk (e.g. ramdisk-recovery.img from the OrangeFox build), or
  * a vendor_boot.img produced by the build (its recovery fragment, or its only fragment, is used).

The recovery fragment MUST stay lz4-legacy: a gzip recovery fragment was tested on a real X6885 and
the recovery then showed a black screen and looped (normal boot was fine). So gzip is not offered.

--fit auto (default)  Keep the build's fragment byte-for-byte if it fits the 64 MiB partition. If it does
                      not fit: drop every regular file that is byte-identical (same path, same mode) to a file in the
                      stock PLATFORM fragment (the platform fragment is always loaded before the recovery fragment, so
                      the merged root filesystem is unchanged), then re-compress with lz4 -12 legacy (the same
                      compressor/format as the Android build, verified to reproduce the build's bytes).
--fit none            Never change the fragment; fail if it does not fit.
Nothing here signs anything, touches vbmeta or disables verification.
"""
import struct, sys, os, stat, ctypes, ctypes.util, hashlib, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unpack_vendor_boot import decompress

PART = 67108864          # stock vendor_boot partition size (64 MiB)
LZ4_MAGIC = bytes.fromhex("02214c18")
al = lambda x, p: (x + p - 1) // p * p

# ------------------------------------------------------------------ vendor_boot parsing
def parse(d):
    assert d[:8] == b"VNDRBOOT", "not a vendor_boot image"
    hv, ps = struct.unpack_from("<II", d, 8)
    assert hv == 4, "need header v4, got %d" % hv
    vrs, = struct.unpack_from("<I", d, 24)
    hs, dtbs = struct.unpack_from("<2I", d, 2096)
    vrts, vrn, vre, bcs = struct.unpack_from("<4I", d, 2112)
    rd = al(hs, ps)
    dtb = rd + al(vrs, ps); tbl = dtb + al(dtbs, ps); bc = tbl + al(vrts, ps)
    frags = []
    for i in range(vrn):
        e = d[tbl + i * vre: tbl + (i + 1) * vre]
        sz, off, typ = struct.unpack_from("<3I", e, 0)
        frags.append(dict(entry=bytearray(e), type=typ, name=e[12:44].split(b"\0")[0].decode(),
                          data=d[rd + off: rd + off + sz]))
    return dict(ps=ps, hdr=bytearray(d[:ps]), dtb=d[dtb:dtb + dtbs], bc=d[bc:bc + bcs], frags=frags,
                vre=vre, end=bc + al(bcs, ps))

def avb_footer(d):
    f = d[-64:]
    if f[:4] != b"AVBf": return None
    _, _, orig, vbo, vbs = struct.unpack(">IIQQQ", f[4:36])
    return dict(orig=orig, off=vbo, size=vbs, blob=d[vbo:vbo + vbs], raw=bytearray(f))

def load_new(path):
    d = open(path, "rb").read()
    if d[:8] == b"VNDRBOOT":
        fr = parse(d)["frags"]
        pick = next((f for f in fr if f["type"] == 2 or f["name"] == "recovery"), None) or \
               (fr[0] if len(fr) == 1 else max(fr, key=lambda f: len(f["data"])))
        print("[*] using fragment name=%r type=%d from build image (%d bytes)" % (pick["name"], pick["type"], len(pick["data"])))
        return pick["data"]
    return d

# ------------------------------------------------------------------ cpio (newc) helpers
def cpio_entries(raw):
    i = 0; out = []
    while True:
        s = i
        assert raw[i:i + 6] == b"070701", "bad cpio magic at %d" % i
        f = [int(raw[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
        mode, nl, fs, nsz = f[1], f[4], f[6], f[11]
        name = raw[i + 110:i + 110 + nsz - 1].decode(); i = (i + 110 + nsz + 3) & ~3
        data = raw[i:i + fs]; i = (i + fs + 3) & ~3
        out.append(dict(name=name, mode=mode, nlink=nl, data=data, raw=raw[s:i]))
        if name == "TRAILER!!!": return out

def norm(n): return n[2:] if n.startswith("./") else n

def merged_view(raws):
    """root fs after unpacking several cpio archives in order (later wins) -> {path: (type, mode, sha1/target)}"""
    view = {}
    for raw in raws:
        for e in cpio_entries(raw):
            if e["name"] == "TRAILER!!!": continue
            m = e["mode"]; t = "d" if stat.S_ISDIR(m) else "l" if stat.S_ISLNK(m) else "f" if stat.S_ISREG(m) else "o"
            view[norm(e["name"])] = (t, m & 0o7777, e["data"].decode() if t == "l" else hashlib.sha1(e["data"]).hexdigest())
    return view

def dedupe(recovery_raw, platform_raws):
    plat = {}
    for raw in platform_raws:
        for e in cpio_entries(raw): plat[norm(e["name"])] = e
    kept = []; dropped = []
    for e in cpio_entries(recovery_raw):
        p = plat.get(norm(e["name"]))
        same = (p is not None and e["name"] != "TRAILER!!!" and stat.S_ISREG(e["mode"]) and stat.S_ISREG(p["mode"])
                and e["mode"] == p["mode"] and e["data"] == p["data"] and len(e["data"]) > 0
                and e["nlink"] == 1 and p["nlink"] == 1)
        (dropped if same else kept).append(e)
    return b"".join(e["raw"] for e in kept), dropped

# ------------------------------------------------------------------ lz4 legacy (HC level 12, 8 MiB blocks)
BLOCK = 8 * 1024 * 1024
def _lib():
    n = ctypes.util.find_library("lz4")
    if not n: return None
    L = ctypes.CDLL(n)
    L.LZ4_compress_HC.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    L.LZ4_compressBound.argtypes = [ctypes.c_int]
    return L

def lz4_legacy(raw, level=12):
    L = _lib()
    if L is not None:
        out = bytearray(LZ4_MAGIC); cap = L.LZ4_compressBound(BLOCK); dst = ctypes.create_string_buffer(cap)
        for i in range(0, len(raw), BLOCK):
            chunk = bytes(raw[i:i + BLOCK]); n = L.LZ4_compress_HC(chunk, dst, len(chunk), cap, level)
            assert n > 0, "LZ4_compress_HC failed"
            out += struct.pack("<I", n) + dst.raw[:n]
        return bytes(out)
    exe = shutil.which("lz4")
    assert exe, "need liblz4 (python ctypes) or the 'lz4' command: sudo apt-get install -y lz4"
    r = subprocess.run([exe, "-l", "-%d" % level, "-c"], input=bytes(raw), capture_output=True)
    assert r.returncode == 0, "lz4 failed: %s" % r.stderr.decode()[:200]
    return r.stdout

# ------------------------------------------------------------------ main
def content_len(s, frag_len, target):
    """size of the image content (before the vbmeta blob) for a given recovery fragment length"""
    ps = s["ps"]
    other = sum(len(f["data"]) for i, f in enumerate(s["frags"]) if i != target)
    tbl = len(s["frags"]) * s["vre"]
    return len(s["hdr"]) + al(other + frag_len, ps) + al(len(s["dtb"]), ps) + al(tbl, ps) + al(len(s["bc"]), ps)

def main(stock_p, new_p, out_p, fit="auto"):
    if fit == "gzip": raise AssertionError("gzip recovery fragments break the recovery on X6885 (tested on device); not supported")
    if fit not in ("auto", "none"): raise AssertionError("--fit must be auto or none")
    stock = open(stock_p, "rb").read()
    s = parse(stock); foot = avb_footer(stock); new = load_new(new_p)
    assert new[:4] == LZ4_MAGIC, "the recovery ramdisk must be lz4-legacy (got magic %s)" % new[:4].hex()
    raw = decompress(new)
    assert raw[:6] == b"070701", "ramdisk is not a newc cpio"
    for need in (b"system/bin/recovery", b"init"):
        assert need in raw, "ramdisk lacks %r - is this really the recovery ramdisk?" % need
    tgt = [i for i, f in enumerate(s["frags"]) if f["type"] == 2 or f["name"] == "recovery"]
    assert len(tgt) == 1, "stock image must have exactly one recovery fragment"
    tgt = tgt[0]
    reserve = (al(foot["size"], 4096) + 64) if foot else 0
    fits = lambda n: content_len(s, n, tgt) + reserve <= PART
    maxfrag = PART - reserve - content_len(s, 0, tgt)
    method = "as built"
    if not fits(len(new)):
        assert fit == "auto", "recovery fragment is %d bytes, the most that fits is %d (use --fit auto)" % (len(new), maxfrag)
        print("[*] recovery fragment is %d bytes; the most that fits is %d. Dropping files identical to the stock platform fragment ..." % (len(new), maxfrag))
        plat_raws = [decompress(f["data"]) for i, f in enumerate(s["frags"]) if i != tgt]
        slim, dropped = dedupe(raw, plat_raws)
        print("    dropped %d files, %.2f MB raw" % (len(dropped), sum(len(e["data"]) for e in dropped) / 1e6))
        # the merged root filesystem must be exactly the same as before
        assert merged_view(plat_raws + [slim]) == merged_view(plat_raws + [raw]), "internal error: merged root fs changed"
        new = lz4_legacy(slim, 12); method = "deduped + lz4 -12"
        assert decompress(new) == slim, "internal error: lz4 round trip mismatch"
        print("    re-compressed: %d bytes" % len(new))
        if not fits(len(new)):
            big = sorted((e for e in cpio_entries(slim) if stat.S_ISREG(e["mode"])), key=lambda e: -len(e["data"]))[:10]
            raise AssertionError("still too big: fragment %d bytes, max %d (over by %d). Largest files: %s" % (
                len(new), maxfrag, len(new) - maxfrag, ", ".join("%s %.1fMB" % (e["name"], len(e["data"]) / 1e6) for e in big)))
    ramdisk = bytearray(); table = bytearray()
    for i, f in enumerate(s["frags"]):
        data = new if i == tgt else f["data"]
        e = bytearray(f["entry"]); struct.pack_into("<II", e, 0, len(data), len(ramdisk))
        table += e; ramdisk += data
        print("  fragment %d %-9r type=%d %9d bytes%s" % (i, f["name"], f["type"], len(data), "  <- REPLACED (%s)" % method if i == tgt else ""))
    ps = s["ps"]; hdr = s["hdr"]; struct.pack_into("<I", hdr, 24, len(ramdisk))
    out = bytearray(hdr)
    for blob in (bytes(ramdisk), s["dtb"], bytes(table), s["bc"]):
        out += blob + b"\0" * (al(len(blob), ps) - len(blob))
    n = len(out)
    if foot:
        assert n + al(foot["size"], 4096) + 64 <= PART, "too big: %d + vbmeta + footer > %d" % (n, PART)
        res = bytearray(out) + b"\0" * (PART - len(out))
        res[n:n + foot["size"]] = foot["blob"]
        f = bytearray(foot["raw"]); struct.pack_into(">QQ", f, 12, n, n)   # original_image_size, vbmeta_offset
        res[-64:] = f
        print("[*] stock AVB footer kept (vbmeta blob moved %d -> %d)" % (foot["off"], n))
    else:
        assert n <= PART, "image exceeds partition"
        res = bytearray(out) + b"\0" * (PART - n)
        print("[!] stock image had no AVB footer; none added")
    open(out_p, "wb").write(res)
    print("[OK] wrote %s: %d bytes (content %d, free %d)" % (out_p, len(res), n, PART - n - reserve))

if __name__ == "__main__":
    args = sys.argv[1:]; fit = "auto"
    if "--fit" in args:
        i = args.index("--fit"); fit = args[i + 1] if i + 1 < len(args) else ""; del args[i:i + 2]
    if len(args) != 3: sys.exit(__doc__)
    try: main(args[0], args[1], args[2], fit)
    except AssertionError as e: sys.exit("ERROR: %s" % e)
