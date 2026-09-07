#!/usr/bin/env python3
"""otool -l/-h-equivalent structure dump for the custom-loader milestone.

Prints every load command, full segment/section detail, the LC_MAIN entry
point, and a static scan of __TEXT for `svc` instructions (address +
immediate) -- everything macho_loader.c needs to have parsed correctly,
laid out for human reading and cross-checking. Reuses tools/macho_patch.py
for header/load-command iteration rather than re-implementing it; adds the
segment/section/entry-point/svc-scan detail macho_patch.py doesn't already
expose (its own commands are patch-focused, not this milestone's
documentation-focused).

Works against any thin arm64 Mach-O; used here against loader/target
(fixtures/hello.s, see loader/build_target.sh) as the concrete example in
docs/custom-loader.md.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import macho_patch as mp  # noqa: E402

LC_MAIN = 0x28 | 0x80000000


def is_svc_instruction(word):
    """Returns (True, imm16) if word is an ARM64 `svc #imm16` instruction.

    Encoding (ARMv8 ARM, "Exception generation" class): bits[31:21] =
    0b11010100000, bits[20:5] = imm16, bits[4:0] = 0b00001. Confirmed
    directly against fixtures/hello.s's own compiled bytes (both `svc
    #0x80` sites decode to word 0xd4001001) before this was trusted."""
    if (word & 0xFFE0001F) == 0xD4000001:
        return True, (word >> 5) & 0xFFFF
    return False, 0


def dump(path):
    with open(path, "rb") as f:
        data = bytearray(f.read())
    header = mp.parse_header(data)
    print("mach_header_64: cputype=%#x cpusubtype=%#x filetype=%d ncmds=%d "
          "sizeofcmds=%d flags=%#x" %
          (header["cputype"] & 0xFFFFFFFF, header["cpusubtype"], header["filetype"],
           header["ncmds"], header["sizeofcmds"], header["flags"]))

    text_vmaddr = None
    entryoff = None
    svc_sites = []

    for i, off, cmd, cmdsize in mp.iter_load_commands(data, header):
        if cmd == mp.LC_SEGMENT_64:
            segname, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects, flags = \
                struct.unpack_from("<16sQQQQiiII", data, off + 8)
            segname = segname.rstrip(b"\x00").decode()
            print("LC_SEGMENT_64 %-12s vmaddr=%#x vmsize=%#x fileoff=%#x "
                  "filesize=%#x maxprot=%d initprot=%d nsects=%d" %
                  (segname, vmaddr, vmsize, fileoff, filesize, maxprot, initprot, nsects))
            if segname == "__TEXT":
                text_vmaddr = vmaddr
            sect_base = off + 8 + 64
            for s in range(nsects):
                sect_off = sect_base + s * 80
                sectname, _segname2, addr, size, sec_offset, align = \
                    struct.unpack_from("<16s16sQQII", data, sect_off)
                sectname = sectname.rstrip(b"\x00").decode()
                print("  section %-12s addr=%#x size=%#x offset=%#x align=%d" %
                      (sectname, addr, size, sec_offset, align))
                if segname == "__TEXT" and (initprot & 0x4):  # VM_PROT_EXECUTE
                    section_bytes = data[sec_offset:sec_offset + size]
                    for w in range(0, len(section_bytes) - 3, 4):
                        word = struct.unpack_from("<I", section_bytes, w)[0]
                        matched, imm = is_svc_instruction(word)
                        if matched:
                            svc_sites.append((addr + w, imm))
        elif cmd == LC_MAIN:
            entryoff, stacksize = struct.unpack_from("<QQ", data, off + 8)
            print("LC_MAIN entryoff=%#x stacksize=%#x" % (entryoff, stacksize))
        elif cmd == mp.LC_LOAD_DYLINKER:
            name_off, = struct.unpack_from("<I", data, off + 8)
            name_end = data.index(b"\x00", off + name_off)
            print("LC_LOAD_DYLINKER: %s" % bytes(data[off + name_off:name_end]).decode())

    if text_vmaddr is not None and entryoff is not None:
        print("entry point vmaddr = __TEXT.vmaddr + entryoff = %#x + %#x = %#x" %
              (text_vmaddr, entryoff, text_vmaddr + entryoff))

    print("svc instructions found: %d" % len(svc_sites))
    for vmaddr, imm in svc_sites:
        print("  vmaddr=%#x imm=%#x" % (vmaddr, imm))
    return svc_sites


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    args = parser.parse_args(argv)
    dump(args.binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
