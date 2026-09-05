#!/usr/bin/env python3
"""Minimal Mach-O platform patcher for the maciOS bring-up milestone.

Rewrites the platform tag of a thin arm64 Mach-O binary (macOS -> iOS) in
place, then recomputes the ad-hoc CodeDirectory page hashes so the file
stays internally consistent. No load command is resized or moved: only the
4-byte platform field (or LC_VERSION_MIN_* command id) and the affected
CodeDirectory hash slots are rewritten. This does not forge a trusted
signing identity -- see docs/testing-on-device.md for why a jailbroken
device (AMFI disabled/patched) is still required to execute the result.
"""
import argparse
import hashlib
import struct
import sys

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
CPU_TYPE_ARM64 = 0x0100000C

LC_SEGMENT_64 = 0x19
LC_CODE_SIGNATURE = 0x1D
LC_VERSION_MIN_MACOSX = 0x24
LC_VERSION_MIN_IPHONEOS = 0x25
LC_VERSION_MIN_TVOS = 0x2F
LC_VERSION_MIN_WATCHOS = 0x30
LC_BUILD_VERSION = 0x32

VERSION_MIN_CMDS = {
    "macos": LC_VERSION_MIN_MACOSX,
    "ios": LC_VERSION_MIN_IPHONEOS,
    "tvos": LC_VERSION_MIN_TVOS,
    "watchos": LC_VERSION_MIN_WATCHOS,
}

PLATFORM_IDS = {
    "macos": 1,
    "ios": 2,
    "tvos": 3,
    "watchos": 4,
    "bridgeos": 5,
    "mac-catalyst": 6,
    "ios-simulator": 7,
    "tvos-simulator": 8,
    "watchos-simulator": 9,
}
PLATFORM_NAMES = {v: k for k, v in PLATFORM_IDS.items()}

CSMAGIC_EMBEDDED_SIGNATURE = 0xFADE0CC0
CSMAGIC_CODEDIRECTORY = 0xFADE0C02

HASH_FUNCS = {
    1: (hashlib.sha1, 20),   # legacy SHA-1
    2: (hashlib.sha256, 32), # SHA-256
    3: (hashlib.sha256, 20), # SHA-256, truncated to 20 bytes
}


class MachOError(Exception):
    pass


def encode_version(text):
    major, minor, patch = (list(map(int, text.split("."))) + [0, 0])[:3]
    return (major << 16) | (minor << 8) | patch


def decode_version(value):
    return "%d.%d.%d" % ((value >> 16) & 0xFFFF, (value >> 8) & 0xFF, value & 0xFF)


def parse_header(data):
    if len(data) < 4:
        raise MachOError("file too small to be a Mach-O")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic in (FAT_MAGIC, FAT_CIGAM):
        raise MachOError(
            "fat/universal binary given; extract the arm64 slice first "
            "(e.g. `lipo -thin arm64 in -output out`)"
        )
    if magic != MH_MAGIC_64:
        raise MachOError("not a 64-bit Mach-O (magic=%#x); only thin arm64 "
                          "MH_MAGIC_64 binaries are supported" % magic)
    (magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags,
     _reserved) = struct.unpack_from("<IiiIIIII", data, 0)
    if cputype != CPU_TYPE_ARM64:
        raise MachOError("cputype %#x is not CPU_TYPE_ARM64; maciOS targets "
                          "native arm64 execution only, no translation" % (cputype & 0xffffffff))
    return {
        "cputype": cputype,
        "cpusubtype": cpusubtype,
        "filetype": filetype,
        "ncmds": ncmds,
        "sizeofcmds": sizeofcmds,
        "flags": flags,
    }


def iter_load_commands(data, header):
    off = 32
    for i in range(header["ncmds"]):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmdsize < 8 or off + cmdsize > len(data):
            raise MachOError("corrupt load command %d at offset %d" % (i, off))
        yield i, off, cmd, cmdsize
        off += cmdsize


def find_platform_command(data, header):
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        if cmd == LC_BUILD_VERSION:
            platform, minos, sdk, ntools = struct.unpack_from("<IIII", data, off + 8)
            return {"kind": "build_version", "offset": off, "cmdsize": cmdsize,
                     "platform": platform, "minos": minos, "sdk": sdk}
        for name, cmd_id in VERSION_MIN_CMDS.items():
            if cmd == cmd_id:
                version, sdk = struct.unpack_from("<II", data, off + 8)
                return {"kind": "version_min", "offset": off, "cmdsize": cmdsize,
                        "platform": PLATFORM_IDS[name], "minos": version, "sdk": sdk}
    return None


def find_code_signature(data, header):
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        if cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from("<II", data, off + 8)
            return dataoff, datasize
    return None


def describe(path):
    with open(path, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    info = {"file": path, "size": len(data), "header": header}
    plat = find_platform_command(data, header)
    if plat:
        info["platform"] = PLATFORM_NAMES.get(plat["platform"], plat["platform"])
        info["platform_kind"] = plat["kind"]
        info["min_os"] = decode_version(plat["minos"])
        info["sdk"] = decode_version(plat["sdk"])
    else:
        info["platform"] = None
    cs = find_code_signature(data, header)
    if cs:
        dataoff, datasize = cs
        info["code_signature"] = {"offset": dataoff, "size": datasize}
        info["code_signature"]["blobs"] = list(_iter_superblob(data, dataoff))
    else:
        info["code_signature"] = None
    return info


def _iter_superblob(data, dataoff):
    magic, length, count = struct.unpack_from(">III", data, dataoff)
    if magic != CSMAGIC_EMBEDDED_SIGNATURE:
        return
    for i in range(count):
        slot_type, blob_off = struct.unpack_from(">II", data, dataoff + 12 + i * 8)
        blob_magic = struct.unpack_from(">I", data, dataoff + blob_off)[0]
        entry = {"slot_type": slot_type, "offset": blob_off, "magic": blob_magic}
        if blob_magic == CSMAGIC_CODEDIRECTORY:
            entry.update(_parse_code_directory(data, dataoff + blob_off))
        yield entry


def _parse_code_directory(data, cd_off):
    (magic, length, version, flags, hashOffset, identOffset, nSpecialSlots,
     nCodeSlots, codeLimit, hashSize, hashType, platform, pageSizeLog2,
     _spare2) = struct.unpack_from(">IIIIIIIIIBBBBI", data, cd_off)
    return {
        "version": version, "flags": flags, "hashOffset": hashOffset,
        "identOffset": identOffset, "nSpecialSlots": nSpecialSlots,
        "nCodeSlots": nCodeSlots, "codeLimit": codeLimit,
        "hashSize": hashSize, "hashType": hashType,
        "cd_platform": platform, "pageSize": 1 << pageSizeLog2 if pageSizeLog2 else 0,
    }


def resign_adhoc(data, header):
    """Recompute CodeDirectory code-slot hashes in place for every embedded
    CodeDirectory blob. Does not touch special slots (entitlements,
    requirements, ...) since their referenced blobs are unchanged. Returns
    the number of CodeDirectory blobs updated, or None if there is no
    signature to update."""
    cs = find_code_signature(data, header)
    if cs is None:
        return None
    dataoff, datasize = cs
    updated = 0
    for entry in _iter_superblob(data, dataoff):
        if entry["magic"] != CSMAGIC_CODEDIRECTORY:
            continue
        cd_off = dataoff + entry["offset"]
        hash_type = entry["hashType"]
        if hash_type not in HASH_FUNCS:
            raise MachOError("unsupported CodeDirectory hashType %d" % hash_type)
        hash_func, digest_len = HASH_FUNCS[hash_type]
        if digest_len != entry["hashSize"]:
            raise MachOError("unexpected hash size %d for hashType %d" %
                              (entry["hashSize"], hash_type))
        page_size = entry["pageSize"] or 4096
        code_limit = entry["codeLimit"]
        hash_off = cd_off + entry["hashOffset"]
        for slot in range(entry["nCodeSlots"]):
            start = slot * page_size
            end = min(start + page_size, code_limit)
            digest = hash_func(bytes(data[start:end])).digest()[:digest_len]
            data[hash_off + slot * digest_len: hash_off + (slot + 1) * digest_len] = digest
        updated += 1
    return updated


def patch_platform(data, header, target_platform, min_os=None, sdk=None):
    plat = find_platform_command(data, header)
    if plat is None:
        raise MachOError("no LC_BUILD_VERSION or LC_VERSION_MIN_* command found; "
                          "cannot determine or change platform")
    target_id = PLATFORM_IDS[target_platform]
    off = plat["offset"]
    if plat["kind"] == "build_version":
        new_minos = encode_version(min_os) if min_os else plat["minos"]
        new_sdk = encode_version(sdk) if sdk else plat["sdk"]
        struct.pack_into("<IIII", data, off + 8, target_id, new_minos, new_sdk,
                          struct.unpack_from("<I", data, off + 8 + 12)[0])
    else:
        if target_platform not in VERSION_MIN_CMDS:
            raise MachOError("legacy LC_VERSION_MIN_* form cannot express "
                              "platform %r; recompile with a toolchain that "
                              "emits LC_BUILD_VERSION" % target_platform)
        new_minos = encode_version(min_os) if min_os else plat["minos"]
        new_sdk = encode_version(sdk) if sdk else plat["sdk"]
        struct.pack_into("<II", data, off, VERSION_MIN_CMDS[target_platform], plat["cmdsize"])
        struct.pack_into("<II", data, off + 8, new_minos, new_sdk)
    return plat


def cmd_info(args):
    info = describe(args.binary)
    print("file:        %s (%d bytes)" % (info["file"], info["size"]))
    h = info["header"]
    print("cputype:     %#x  filetype: %d  ncmds: %d" % (h["cputype"] & 0xffffffff, h["filetype"], h["ncmds"]))
    if info["platform"]:
        print("platform:    %s (via %s)" % (info["platform"], info["platform_kind"]))
        print("min_os/sdk:  %s / %s" % (info["min_os"], info["sdk"]))
    else:
        print("platform:    <none found>")
    cs = info["code_signature"]
    if cs:
        print("code sig:    offset=%d size=%d" % (cs["offset"], cs["size"]))
        for blob in cs["blobs"]:
            if blob["magic"] == CSMAGIC_CODEDIRECTORY:
                print("  CodeDirectory: flags=%#x hashType=%d nCodeSlots=%d "
                      "nSpecialSlots=%d codeLimit=%d" %
                      (blob["flags"], blob["hashType"], blob["nCodeSlots"],
                       blob["nSpecialSlots"], blob["codeLimit"]))
    else:
        print("code sig:    <none>")
    return 0


def cmd_patch(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    before = find_platform_command(data, header)
    if before:
        print("before: platform=%s min_os=%s sdk=%s" %
              (PLATFORM_NAMES.get(before["platform"], before["platform"]),
               decode_version(before["minos"]), decode_version(before["sdk"])))
    patch_platform(data, header, args.platform, args.min_os, args.sdk)
    after = find_platform_command(data, header)
    print("after:  platform=%s min_os=%s sdk=%s" %
          (PLATFORM_NAMES.get(after["platform"], after["platform"]),
           decode_version(after["minos"]), decode_version(after["sdk"])))
    if args.resign:
        updated = resign_adhoc(data, header)
        if updated is None:
            print("warning: no LC_CODE_SIGNATURE found; nothing to resign")
        else:
            print("resigned %d CodeDirectory blob(s) in place (ad hoc, no trust chain)" % updated)
    else:
        print("warning: --no-resign given; existing signature hashes are now "
              "invalid and the binary will not run without external resigning")
    out_path = args.output or args.binary
    with open(out_path, "wb") as f:
        f.write(data)
    print("wrote", out_path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="print Mach-O platform/signature info")
    p_info.add_argument("binary")
    p_info.set_defaults(func=cmd_info)

    p_patch = sub.add_parser("patch", help="rewrite the platform tag in place")
    p_patch.add_argument("binary")
    p_patch.add_argument("-o", "--output", help="output path (default: overwrite input)")
    p_patch.add_argument("--platform", required=True, choices=sorted(PLATFORM_IDS))
    p_patch.add_argument("--min-os", help="e.g. 12.0.0 (default: keep existing)")
    p_patch.add_argument("--sdk", help="e.g. 12.0.0 (default: keep existing)")
    p_patch.add_argument("--no-resign", dest="resign", action="store_false",
                          help="skip recomputing ad-hoc CodeDirectory hashes")
    p_patch.set_defaults(func=cmd_patch, resign=True)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MachOError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
