#!/usr/bin/env python3
"""Minimal Mach-O patcher for the maciOS bring-up milestone.

Two independent core operations, plus dependency-resolution tooling
(deps/check-deps/redirect-deps -- see docs/dependency-resolution.md) for
M3's "what does this real binary actually need" question:

  patch     Rewrite the platform tag of a thin arm64 Mach-O (macOS -> iOS)
            in place, then recompute the ad-hoc CodeDirectory page hashes so
            the file stays internally consistent. No load command is
            resized or moved: only the 4-byte platform field (or
            LC_VERSION_MIN_* command id) and the affected CodeDirectory hash
            slots are rewritten.

  dylibify  Convert an MH_EXECUTE into something dlopen() will accept as a
            library: flip the filetype, neutralize __PAGEZERO, and write an
            LC_ID_DYLIB into the space the original LC_LOAD_DYLINKER
            occupied (a dylib has no use for a dynamic-linker path, and
            that command is already sized to hold a short replacement).

Neither operation forges a trusted signing identity: an ad-hoc resign here
only keeps the file's own signature internally consistent (useful for local
inspection), it does not make the result installable on its own. Getting a
patched binary to actually run on a non-jailbroken device needs a real
signing identity applied to the whole bundle at install time -- see
AUDIT.md section 2 for why that works and docs/xcode-setup.md /
docs/process-host.md for the two ways this repo uses the result (recompiled
source vs. an already-compiled binary loaded by process-host/).
"""
import argparse
import hashlib
import os
import struct
import subprocess
import sys

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
CPU_TYPE_ARM64 = 0x0100000C

MH_EXECUTE = 0x2
MH_DYLIB = 0x6

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
LC_LOAD_DYLINKER = 0xE
LC_ID_DYLIB = 0xD
LC_CODE_SIGNATURE = 0x1D
LC_LOAD_DYLIB = 0xC
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LOAD_UPWARD_DYLIB = 0x80000023
DYLIB_LOAD_KINDS = {
    LC_LOAD_DYLIB: "required",
    LC_LOAD_WEAK_DYLIB: "weak",
    LC_REEXPORT_DYLIB: "reexport",
    LC_LOAD_UPWARD_DYLIB: "upward",
}
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


def _text_padding_available(data, header):
    """Bytes of unused space between the end of the load commands and the
    first file-backed section of __TEXT -- i.e. how much room there is to
    grow the load commands in place without relocating anything after them.
    Compilers leave this gap because segments are page-aligned."""
    min_section_offset = None
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        if cmd != LC_SEGMENT_64:
            continue
        segname = bytes(data[off + 8: off + 24]).rstrip(b"\x00")
        if segname != b"__TEXT":
            continue
        nsects = struct.unpack_from("<I", data, off + 64)[0]
        sect_base = off + 72  # sizeof(segment_command_64)
        for s in range(nsects):
            sect_off = sect_base + s * 80  # sizeof(section_64)
            sect_file_offset = struct.unpack_from("<I", data, sect_off + 48)[0]
            if sect_file_offset == 0:
                continue  # zerofill section, not backed by file data
            if min_section_offset is None or sect_file_offset < min_section_offset:
                min_section_offset = sect_file_offset
    end_of_cmds = 32 + header["sizeofcmds"]
    if min_section_offset is None:
        return 0
    return min_section_offset - end_of_cmds


def dylibify(data, header, install_name):
    """Convert an MH_EXECUTE into something dlopen() will accept as a library:
    flip the filetype to MH_DYLIB, neutralize __PAGEZERO (meaningless for a
    binary being mapped into an already-running process), and give it an
    LC_ID_DYLIB -- confirmed on real hardware (see AUDIT.md) that dyld
    refuses an MH_DYLIB without one, even though the filetype flip alone is
    otherwise accepted.

    LC_ID_DYLIB is written into the existing LC_LOAD_DYLINKER command's
    space rather than appended: a dylib has no use for LC_LOAD_DYLINKER
    (that's an executable's pointer to /usr/lib/dyld), and it's already
    sized to hold a 24-byte header plus a short name, so this needs no
    load-command relocation. If no LC_LOAD_DYLINKER is present, this fails
    rather than risk writing a corrupt binary."""
    if header["filetype"] != MH_EXECUTE:
        raise MachOError("filetype is %#x, not MH_EXECUTE -- already a dylib?" %
                          header["filetype"])

    dylinker_off = dylinker_cmdsize = None
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        if cmd == LC_SEGMENT_64:
            segname = bytes(data[off + 8: off + 24]).rstrip(b"\x00")
            if segname == b"__PAGEZERO":
                struct.pack_into("<Q", data, off + 32, 0)  # vmsize
        elif cmd == LC_LOAD_DYLINKER and dylinker_off is None:
            dylinker_off, dylinker_cmdsize = off, cmdsize

    if dylinker_off is None:
        raise MachOError("no LC_LOAD_DYLINKER to repurpose as LC_ID_DYLIB; "
                          "full load-command relocation isn't implemented")
    name = install_name.encode("utf-8") + b"\x00"
    if 24 + len(name) > dylinker_cmdsize:
        # Not fixture-specific: LC_LOAD_DYLINKER always names /usr/lib/dyld,
        # for any normally-linked Mach-O executable, so this slot is capped
        # at roughly the same tiny size (~8 bytes) regardless of what real
        # binary is being converted. Confirmed against a real cross-compiled
        # macOS executable, not just this repo's own fixtures.
        raise MachOError("install name %r too long for the %d bytes available "
                          "(this slot is always small -- LC_LOAD_DYLINKER "
                          "names /usr/lib/dyld for any normal executable, "
                          "not just this one)" %
                          (install_name, dylinker_cmdsize - 24))
    for i in range(dylinker_off, dylinker_off + dylinker_cmdsize):
        data[i] = 0
    struct.pack_into("<IIIIII", data, dylinker_off,
                      LC_ID_DYLIB, dylinker_cmdsize, 24, 0, 0x00010000, 0x00010000)
    data[dylinker_off + 24: dylinker_off + 24 + len(name)] = name

    struct.pack_into("<I", data, 12, MH_DYLIB)


N_EXT = 0x01
N_TYPE = 0x0E
N_UNDF = 0x00


def list_symbols(data, header):
    """Yield {'name', 'external', 'defined', 'value', 'library_ordinal'} for
    every symbol in the file's LC_SYMTAB. Useful for confirming a specific
    entry point survives patch/dylibify with external linkage intact --
    e.g. that dlsym() will actually be able to find it -- rather than just
    assuming a byte-level patch didn't disturb the symbol table.

    library_ordinal is the two-level-namespace ordinal from n_desc (1-based
    index into the file's LC_LOAD_DYLIB-family commands, in the order
    list_dylib_dependencies yields them -- see undefined_symbols_by_dependency,
    which is what actually uses this)."""
    symoff = nsyms = stroff = None
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, _strsize = struct.unpack_from("<IIII", data, off + 8)
            break
    if symoff is None:
        return
    for i in range(nsyms):
        entry_off = symoff + i * 16  # sizeof(struct nlist_64)
        n_strx, n_type, _n_sect, n_desc, n_value = struct.unpack_from(
            "<IBBHQ", data, entry_off)
        name_off = stroff + n_strx
        name_end = data.index(b"\x00", name_off)
        name = bytes(data[name_off:name_end]).decode("utf-8", "replace")
        yield {
            "name": name,
            "external": bool(n_type & N_EXT),
            "defined": (n_type & N_TYPE) != N_UNDF,
            "value": n_value,
            "library_ordinal": (n_desc >> 8) & 0xFF,
        }


def undefined_symbols_by_dependency(data, header):
    """Groups undefined (imported) external symbols by which
    LC_LOAD_DYLIB-family dependency they're resolved against, via each
    symbol's two-level-namespace library ordinal. This is the exact,
    precise list a stub replacing that dependency would need to export --
    not a guess at a whole framework's surface, just the handful of symbols
    this specific binary actually calls. Ordinals outside the dependency
    list (SELF_LIBRARY_ORDINAL=0, DYNAMIC_LOOKUP_ORDINAL=0xfe,
    EXECUTABLE_ORDINAL=0xff) are skipped -- they don't name a real
    dependency to stub."""
    deps = list(list_dylib_dependencies(data, header))
    by_ordinal = {}
    for sym in list_symbols(data, header):
        if sym["defined"] or not sym["external"]:
            continue
        ordinal = sym["library_ordinal"]
        if ordinal < 1 or ordinal > len(deps):
            continue
        by_ordinal.setdefault(ordinal, []).append(sym["name"])
    return {deps[ordinal - 1]["path"]: sorted(names) for ordinal, names in by_ordinal.items()}


def list_dylib_dependencies(data, header):
    """Yield {'kind', 'path', 'current_version', 'compatibility_version',
    'offset', 'cmdsize', 'name_offset'} for every LC_LOAD_DYLIB-family load
    command -- the binary's own declared runtime dependencies (frameworks
    and dylibs dyld will need to resolve when it's actually run). This is
    the starting point for M3's dependency-resolution work (MILESTONES.md):
    before porting a real macOS binary, find out what it actually links
    against and which of those exist on iOS at all."""
    for i, off, cmd, cmdsize in iter_load_commands(data, header):
        kind = DYLIB_LOAD_KINDS.get(cmd)
        if kind is None:
            continue
        name_off, timestamp, current_version, compat_version = struct.unpack_from(
            "<IIII", data, off + 8)
        name_start = off + name_off
        name_end = data.index(b"\x00", name_start)
        path = bytes(data[name_start:name_end]).decode("utf-8", "replace")
        yield {
            "kind": kind,
            "path": path,
            "current_version": decode_version(current_version),
            "compatibility_version": decode_version(compat_version),
            "offset": off,
            "cmdsize": cmdsize,
            "name_offset": name_off,
        }


def redirect_dylib_dependency(data, header, old_path, new_path):
    """Rewrite one LC_LOAD_DYLIB-family command's path string in place --
    e.g. pointing a macOS-only framework's install name at an iOS
    equivalent under a different path (OpenGL.framework -> OpenGLES.framework
    is the classic example), or at a stub dylib bundled inside the app for
    something iOS has no equivalent of at all. See docs/dependency-resolution.md.

    Never grows or relocates load commands -- the replacement must fit in
    the existing command's size, the same constraint dylibify hits with
    LC_LOAD_DYLINKER's slot (cmdsize is fixed at compile time; a longer
    replacement needs full load-command relocation, not implemented here)."""
    match = None
    for dep in list_dylib_dependencies(data, header):
        if dep["path"] == old_path:
            match = dep
            break
    if match is None:
        raise MachOError("no LC_LOAD_DYLIB-family dependency with path %r found" % old_path)

    off = match["offset"]
    cmdsize = match["cmdsize"]
    name_off = match["name_offset"]
    available = cmdsize - name_off - 1  # room for the new name, minus its NUL
    new_name = new_path.encode("utf-8")
    if len(new_name) > available:
        raise MachOError(
            "redirect path %r (%d bytes) doesn't fit in the %d bytes available "
            "in %r's dependency load command (cmdsize is fixed at compile "
            "time; a longer replacement needs full load-command relocation, "
            "not implemented here)" % (new_path, len(new_name), available, old_path))
    name_region_start = off + name_off
    name_region_end = off + cmdsize
    for i in range(name_region_start, name_region_end):
        data[i] = 0
    data[name_region_start:name_region_start + len(new_name)] = new_name
    return match


# Best-effort seed list for offline use (this repo's own Linux sandbox has
# no iOS SDK at all to check against). Keyed by the framework/dylib's own
# basename so it matches regardless of which OS version's path is in the
# header. When an --ios-sdk-path is available (e.g. the real macOS CI
# runner, which has one at a known Xcode location -- see
# _detect_ios_sdk_path), that's the authoritative source instead: this
# table is only ever a guess, and is deliberately non-exhaustive rather
# than confidently wrong about anything not looked up directly against a
# real SDK. See docs/dependency-resolution.md for how each entry here was
# decided and where to add more once a real M3 target names them.
KNOWN_IOS_AVAILABILITY = {
    # Available on iOS -- same framework/dylib name, present in the SDK.
    "libSystem.B.dylib": True, "libobjc.A.dylib": True, "libc++.1.dylib": True,
    "libc++abi.dylib": True, "libz.1.dylib": True, "libsqlite3.dylib": True,
    "libxml2.2.dylib": True, "libcompression.dylib": True,
    "Foundation": True, "CoreFoundation": True, "CoreGraphics": True,
    "CoreText": True, "CoreImage": True, "Security": True, "Network": True,
    "CFNetwork": True, "SystemConfiguration": True, "CoreAudio": True,
    "AudioToolbox": True, "AVFoundation": True, "CoreMedia": True,
    "ImageIO": True, "QuartzCore": True, "LocalAuthentication": True,
    "Combine": True, "SwiftUI": True, "CryptoKit": True,
    "UniformTypeIdentifiers": True, "WebKit": True, "PDFKit": True,
    "Metal": True, "MetalKit": True, "StoreKit": True,
    # macOS-only -- no iOS counterpart at all (needs a stub, or the
    # functionality dropped if the payload can tolerate that).
    "AppKit": False, "Cocoa": False, "Carbon": False, "CoreServices": False,
    "DiskArbitration": False, "ServiceManagement": False, "IOBluetooth": False,
    "IOKit": False, "Quartz": False, "ScriptingBridge": False,
    "OpenDirectory": False, "Automator": False, "PreferencePanes": False,
    "InstallerPlugins": False, "CoreWLAN": False, "SecurityInterface": False,
    "OpenGL": False,  # iOS has OpenGLES.framework instead -- a redirect target
}


def _detect_ios_sdk_path():
    """Only works on a real macOS toolchain (e.g. the CI runner in
    .github/workflows/build.yml) -- returns None in this repo's own Linux
    sandbox, where there is no Xcode/xcrun at all."""
    try:
        result = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"],
                                 capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return path or None


def classify_dependency(path, ios_sdk_path=None):
    """Returns (status, detail): status is 'available', 'unavailable', or
    'unknown'. Verified directly against a real iOS SDK's on-disk layout
    when ios_sdk_path is given (the authoritative source -- SDKs mirror the
    runtime's absolute framework/dylib paths under their own root, e.g.
    <sdk>/System/Library/Frameworks/Foundation.framework/Foundation). Falls
    back to the best-effort KNOWN_IOS_AVAILABILITY table otherwise, which is
    what to trust only until a real SDK is available to check instead."""
    basename = path.rsplit("/", 1)[-1]
    if ios_sdk_path:
        candidate = os.path.join(ios_sdk_path, path.lstrip("/"))
        exists = os.path.exists(candidate)
        return ("available" if exists else "unavailable",
                "verified against %s" % ios_sdk_path)
    known = KNOWN_IOS_AVAILABILITY.get(basename)
    if known is None:
        return ("unknown", "not in the built-in seed list -- check manually "
                            "or pass --ios-sdk-path")
    return ("available" if known else "unavailable", "best-effort guess, not SDK-verified")


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


def cmd_symbols(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    found = False
    for sym in list_symbols(data, header):
        if args.defined_only and not sym["defined"]:
            continue
        if args.grep and args.grep not in sym["name"]:
            continue
        found = True
        print("%-40s external=%d defined=%d value=%#x" %
              (sym["name"], sym["external"], sym["defined"], sym["value"]))
    if args.grep and not found:
        print("error: no symbol matching %r found" % args.grep, file=sys.stderr)
        return 1
    return 0


def cmd_deps(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    found = False
    for dep in list_dylib_dependencies(data, header):
        found = True
        print("%-9s %s (current=%s compatibility=%s)" %
              (dep["kind"], dep["path"], dep["current_version"], dep["compatibility_version"]))
    if not found:
        print("(no LC_LOAD_DYLIB-family dependencies found)")
    return 0


def cmd_check_deps(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)

    ios_sdk_path = args.ios_sdk_path
    if not ios_sdk_path and args.auto_detect_sdk:
        ios_sdk_path = _detect_ios_sdk_path()
        if ios_sdk_path:
            print("auto-detected iOS SDK: %s" % ios_sdk_path)
        else:
            print("warning: --auto-detect-sdk given but no iOS SDK found "
                  "(no xcrun, or this isn't a macOS toolchain) -- falling "
                  "back to the built-in best-effort table")

    unavailable = []
    unknown = []
    any_dep = False
    for dep in list_dylib_dependencies(data, header):
        any_dep = True
        status, detail = classify_dependency(dep["path"], ios_sdk_path)
        print("%-12s %-9s %s (%s)" % (status, dep["kind"], dep["path"], detail))
        if status == "unavailable":
            unavailable.append(dep["path"])
        elif status == "unknown":
            unknown.append(dep["path"])
    if not any_dep:
        print("(no LC_LOAD_DYLIB-family dependencies found)")

    if unavailable:
        by_dep = undefined_symbols_by_dependency(data, header)
        print("\n%d dependenc%s with no iOS counterpart -- needs a redirect "
              "or a stub (see docs/dependency-resolution.md):" %
              (len(unavailable), "y" if len(unavailable) == 1 else "ies"))
        for p in unavailable:
            print("  -", p)
            for sym in by_dep.get(p, []):
                print("      needs:", sym)
    if unknown:
        print("\n%d dependenc%s not classified -- check manually, or pass "
              "--ios-sdk-path/--auto-detect-sdk for a verified answer:" %
              (len(unknown), "y" if len(unknown) == 1 else "ies"))
        for p in unknown:
            print("  -", p)

    if unavailable and not args.allow_unavailable:
        return 1
    return 0


def cmd_redirect_deps(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    for mapping in args.redirect:
        if "=" not in mapping:
            print("error: --redirect expects OLDPATH=NEWPATH, got %r" % mapping, file=sys.stderr)
            return 1
        old_path, new_path = mapping.split("=", 1)
        match = redirect_dylib_dependency(data, header, old_path, new_path)
        print("redirected %s -> %s (kind=%s)" % (old_path, new_path, match["kind"]))
    if args.resign:
        updated = resign_adhoc(data, header)
        if updated is None:
            print("warning: no LC_CODE_SIGNATURE found; nothing to resign")
        else:
            print("resigned %d CodeDirectory blob(s) in place (ad hoc, no trust chain)" % updated)
    else:
        print("warning: --no-resign given; existing signature hashes are now invalid")
    out_path = args.output or args.binary
    with open(out_path, "wb") as f:
        f.write(data)
    print("wrote", out_path)
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


def cmd_dylibify(args):
    with open(args.binary, "rb") as f:
        data = bytearray(f.read())
    header = parse_header(data)
    dylibify(data, header, args.install_name or args.binary.rsplit("/", 1)[-1])
    print("filetype: MH_EXECUTE -> MH_DYLIB, __PAGEZERO neutralized, "
          "LC_ID_DYLIB \"%s\" written into the old LC_LOAD_DYLINKER slot" %
          (args.install_name or args.binary.rsplit("/", 1)[-1]))
    if args.resign:
        header = parse_header(data)  # filetype changed; re-read before resigning
        updated = resign_adhoc(data, header)
        if updated is None:
            print("warning: no LC_CODE_SIGNATURE found; nothing to resign")
        else:
            print("resigned %d CodeDirectory blob(s) in place (ad hoc, no trust chain -- "
                  "if this is going into an app bundle for SideStore to install, that "
                  "resign is discarded and replaced anyway; --no-resign skips it)" % updated)
    else:
        print("warning: --no-resign given; existing signature hashes are now invalid")
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

    p_dylibify = sub.add_parser(
        "dylibify", help="convert MH_EXECUTE -> MH_DYLIB so dlopen() will accept it")
    p_dylibify.add_argument("binary")
    p_dylibify.add_argument("-o", "--output", help="output path (default: overwrite input)")
    p_dylibify.add_argument("--install-name", help="LC_ID_DYLIB name (default: input filename)")
    p_dylibify.add_argument("--no-resign", dest="resign", action="store_false",
                             help="skip recomputing ad-hoc CodeDirectory hashes")
    p_dylibify.set_defaults(func=cmd_dylibify, resign=True)

    p_symbols = sub.add_parser(
        "symbols", help="list LC_SYMTAB symbols (e.g. to confirm an entry point survived patching)")
    p_symbols.add_argument("binary")
    p_symbols.add_argument("--grep", help="only show symbols containing this substring; "
                                           "exit 1 if none match (for CI scripting)")
    p_symbols.add_argument("--defined-only", action="store_true",
                            help="skip undefined (imported) symbols")
    p_symbols.set_defaults(func=cmd_symbols)

    p_deps = sub.add_parser(
        "deps", help="list LC_LOAD_DYLIB-family dependencies (frameworks/dylibs the binary needs)")
    p_deps.add_argument("binary")
    p_deps.set_defaults(func=cmd_deps)

    p_check_deps = sub.add_parser(
        "check-deps", help="classify each dependency as available/unavailable on iOS")
    p_check_deps.add_argument("binary")
    p_check_deps.add_argument("--ios-sdk-path", help="check against a real iOS SDK's on-disk layout "
                                                       "(authoritative) instead of the built-in guess table")
    p_check_deps.add_argument("--auto-detect-sdk", action="store_true",
                               help="try `xcrun --sdk iphoneos --show-sdk-path` if --ios-sdk-path is not given "
                                    "(only works on a real macOS toolchain, e.g. CI)")
    p_check_deps.add_argument("--allow-unavailable", action="store_true",
                               help="exit 0 even if an unavailable dependency is found (default: exit 1, for CI gating)")
    p_check_deps.set_defaults(func=cmd_check_deps)

    p_redirect_deps = sub.add_parser(
        "redirect-deps", help="rewrite a dependency's path in place (e.g. to an iOS equivalent or a bundled stub)")
    p_redirect_deps.add_argument("binary")
    p_redirect_deps.add_argument("-o", "--output", help="output path (default: overwrite input)")
    p_redirect_deps.add_argument("--redirect", action="append", required=True, metavar="OLDPATH=NEWPATH",
                                  help="may be given more than once")
    p_redirect_deps.add_argument("--no-resign", dest="resign", action="store_false",
                                  help="skip recomputing ad-hoc CodeDirectory hashes")
    p_redirect_deps.set_defaults(func=cmd_redirect_deps, resign=True)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MachOError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
