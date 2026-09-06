import shutil
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import macho_patch as mp  # noqa: E402

FIXTURES = ROOT / "fixtures"
HAVE_TOOLCHAIN = shutil.which("clang") and (shutil.which("ld.lld") or shutil.which("ld64.lld"))


def build_fixture(tmp_path, source="hello.s", out_name="hello_macos_arm64"):
    out = tmp_path / out_name
    subprocess.run(
        ["clang", "-target", "arm64-apple-macos11", "-fuse-ld=lld", "-nostdlib",
         "-Wl,-e,__start", "-Wl,-platform_version,macos,11.0,11.0",
         str(FIXTURES / source), "-o", str(out)],
        check=True, capture_output=True,
    )
    return out


@unittest.skipUnless(HAVE_TOOLCHAIN, "clang+lld not available to build a real Mach-O fixture")
class TestAgainstRealBinary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._get_tmp_dir())
        self.binary = build_fixture(self.tmp)

    def _get_tmp_dir(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_info_reports_macos_platform(self):
        info = mp.describe(str(self.binary))
        self.assertEqual(info["platform"], "macos")
        self.assertEqual(info["min_os"], "11.0.0")
        self.assertIsNotNone(info["code_signature"])

    def test_patch_to_ios_keeps_file_size_and_valid_hashes(self):
        original = self.binary.read_bytes()
        data = bytearray(original)
        header = mp.parse_header(data)
        mp.patch_platform(data, header, "ios", min_os="12.0.0", sdk="12.0.0")
        updated = mp.resign_adhoc(data, header)
        self.assertEqual(updated, 1)
        self.assertEqual(len(data), len(original))

        after = mp.find_platform_command(data, header)
        self.assertEqual(after["platform"], mp.PLATFORM_IDS["ios"])
        self.assertEqual(after["minos"], mp.encode_version("12.0.0"))

        self._assert_hashes_valid(data, header)

    def test_unresigned_patch_breaks_hashes(self):
        data = bytearray(self.binary.read_bytes())
        header = mp.parse_header(data)
        mp.patch_platform(data, header, "ios")
        with self.assertRaises(AssertionError):
            self._assert_hashes_valid(data, header)

    def test_dylibify_produces_loadable_dylib(self):
        original = self.binary.read_bytes()
        data = bytearray(original)
        header = mp.parse_header(data)
        self.assertEqual(header["filetype"], mp.MH_EXECUTE)

        mp.dylibify(data, header, "h.dylib")
        self.assertEqual(len(data), len(original))

        header = mp.parse_header(data)
        self.assertEqual(header["filetype"], mp.MH_DYLIB)

        found_id_dylib = False
        found_dylinker = False
        for i, off, cmd, cmdsize in mp.iter_load_commands(data, header):
            if cmd == mp.LC_ID_DYLIB:
                found_id_dylib = True
                name_off = struct.unpack_from("<I", data, off + 8)[0]
                name = bytes(data[off + name_off:off + cmdsize]).split(b"\x00", 1)[0]
                self.assertEqual(name, b"h.dylib")
            if cmd == mp.LC_LOAD_DYLINKER:
                found_dylinker = True
        self.assertTrue(found_id_dylib, "expected LC_ID_DYLIB after dylibify")
        self.assertFalse(found_dylinker, "LC_LOAD_DYLINKER should have been repurposed")

        mp.resign_adhoc(data, header)
        self._assert_hashes_valid(data, header)

    def test_dylibify_rejects_non_execute(self):
        data = bytearray(self.binary.read_bytes())
        header = mp.parse_header(data)
        mp.dylibify(data, header, "h.dylib")
        header = mp.parse_header(data)
        with self.assertRaises(mp.MachOError):
            mp.dylibify(data, header, "again.dylib")

    def test_dylibify_rejects_name_too_long_for_slot(self):
        data = bytearray(self.binary.read_bytes())
        header = mp.parse_header(data)
        with self.assertRaises(mp.MachOError):
            mp.dylibify(data, header, "a-considerably-too-long-install-name-for-the-slot.dylib")

    def _assert_hashes_valid(self, data, header):
        dataoff, _ = mp.find_code_signature(data, header)
        for entry in mp._iter_superblob(data, dataoff):
            if entry["magic"] != mp.CSMAGIC_CODEDIRECTORY:
                continue
            cd_off = dataoff + entry["offset"]
            hash_func, digest_len = mp.HASH_FUNCS[entry["hashType"]]
            page_size = entry["pageSize"] or 4096
            hash_off = cd_off + entry["hashOffset"]
            for slot in range(entry["nCodeSlots"]):
                start = slot * page_size
                end = min(start + page_size, entry["codeLimit"])
                expected = hash_func(bytes(data[start:end])).digest()[:digest_len]
                actual = bytes(data[hash_off + slot * digest_len: hash_off + (slot + 1) * digest_len])
                self.assertEqual(expected, actual, "hash mismatch at slot %d" % slot)


@unittest.skipUnless(HAVE_TOOLCHAIN, "clang+lld not available to build a real Mach-O fixture")
class TestFullPipelineAgainstRealPayload(unittest.TestCase):
    """This is the actual end-to-end claim behind M2 (MILESTONES.md):
    compile something for macOS (never for iOS), run it through the exact
    patch + dylibify pipeline a real foreign binary would go through, and
    confirm the entry point process-host/ needs to dlsym is still there,
    externally defined, afterward. TestAgainstRealBinary above checks the
    container (platform tag, filetype, signature); this checks the
    payload itself survives."""

    def setUp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.binary = build_fixture(Path(d), source="macos_payload.c",
                                     out_name="macos_payload_macos_arm64")

    def test_entry_point_survives_patch_and_dylibify(self):
        data = bytearray(self.binary.read_bytes())
        header = mp.parse_header(data)
        self.assertEqual(mp.PLATFORM_NAMES[mp.find_platform_command(data, header)["platform"]],
                          "macos")

        mp.patch_platform(data, header, "ios", min_os="15.0.0", sdk="15.0.0")
        # Install name slot is tiny for any normally-linked executable
        # (LC_LOAD_DYLINKER always names /usr/lib/dyld) -- "p.dylib" is
        # deliberately short, same constraint hit preparing the real
        # on-device test.
        mp.dylibify(data, header, "p.dylib")
        header = mp.parse_header(data)
        self.assertEqual(header["filetype"], mp.MH_DYLIB)

        symbols = {s["name"]: s for s in mp.list_symbols(data, header)}
        entry = symbols.get("_maciOS_patched_payload_entry")
        self.assertIsNotNone(entry, "entry point missing from symbol table after patching: %r" %
                              sorted(symbols))
        self.assertTrue(entry["external"], "entry point must be externally linked for dlsym()")
        self.assertTrue(entry["defined"], "entry point must be defined, not just referenced")


class TestSyntheticMachO(unittest.TestCase):
    """Exercises the parser/patcher on a hand-built minimal Mach-O so the
    core logic is covered even without a Darwin-capable toolchain."""

    def _build_minimal_macho(self):
        # mach_header_64 + one LC_BUILD_VERSION load command, nothing else.
        # Not a runnable binary -- just enough structure for the parser.
        platform, minos, sdk, ntools = 1, 0x0B0000, 0x0B0000, 0
        build_version = struct.pack("<IIIIII", mp.LC_BUILD_VERSION, 24,
                                     platform, minos, sdk, ntools)
        ncmds = 1
        sizeofcmds = len(build_version)
        header = struct.pack("<IiiIIIII", mp.MH_MAGIC_64, mp.CPU_TYPE_ARM64, 0,
                              2, ncmds, sizeofcmds, 0, 0)
        return bytearray(header + build_version)

    def test_parse_header_rejects_non_arm64(self):
        data = self._build_minimal_macho()
        struct.pack_into("<i", data, 4, 0x01000007)  # CPU_TYPE_X86_64
        with self.assertRaises(mp.MachOError):
            mp.parse_header(data)

    def test_find_and_patch_build_version(self):
        data = self._build_minimal_macho()
        header = mp.parse_header(data)
        plat = mp.find_platform_command(data, header)
        self.assertEqual(plat["kind"], "build_version")
        self.assertEqual(plat["platform"], mp.PLATFORM_IDS["macos"])

        mp.patch_platform(data, header, "ios", min_os="12.0.0", sdk="12.1.2")
        after = mp.find_platform_command(data, header)
        self.assertEqual(after["platform"], mp.PLATFORM_IDS["ios"])
        self.assertEqual(after["minos"], mp.encode_version("12.0.0"))
        self.assertEqual(after["sdk"], mp.encode_version("12.1.2"))
        # patching must not change file length or the command's size field
        self.assertEqual(len(data), len(self._build_minimal_macho()))

    def test_version_round_trip(self):
        for text in ("11.0.0", "12.3.4", "0.0.1"):
            self.assertEqual(mp.decode_version(mp.encode_version(text)), text)

    def test_fat_binary_rejected(self):
        data = bytearray(struct.pack(">I", mp.FAT_MAGIC) + b"\x00" * 32)
        with self.assertRaises(mp.MachOError):
            mp.parse_header(data)


if __name__ == "__main__":
    unittest.main()
