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


class TestDependencyResolution(unittest.TestCase):
    """M3's actual first question (MILESTONES.md): what does a real macOS
    binary link against, and which of those exist on iOS? Uses a hand-built
    Mach-O (like TestSyntheticMachO) rather than a real compiled one --
    this repo's Linux sandbox has no way to link against a real framework
    at all (no macOS SDK), which is exactly why classify_dependency's
    ios_sdk_path-verified path can only be exercised on the real macOS CI
    runner, not here."""

    def _build_macho_with_dependency(self, path=b"/System/Library/Frameworks/Foundation.framework/Foundation",
                                      current_version="1.0.0", compat_version="1.0.0",
                                      kind_cmd=None):
        platform, minos, sdk, ntools = 1, 0x0B0000, 0x0B0000, 0
        build_version = struct.pack("<IIIIII", mp.LC_BUILD_VERSION, 24,
                                     platform, minos, sdk, ntools)

        name = path + b"\x00"
        header_size = 24  # cmd, cmdsize, name_offset, timestamp, current, compat
        raw_size = header_size + len(name)
        padded_size = (raw_size + 7) // 8 * 8
        dylib_cmd = struct.pack("<IIIIII", kind_cmd or mp.LC_LOAD_DYLIB, padded_size,
                                 header_size, 0, mp.encode_version(current_version),
                                 mp.encode_version(compat_version))
        dylib_cmd += name
        dylib_cmd += b"\x00" * (padded_size - len(dylib_cmd))

        ncmds = 2
        sizeofcmds = len(build_version) + len(dylib_cmd)
        header = struct.pack("<IiiIIIII", mp.MH_MAGIC_64, mp.CPU_TYPE_ARM64, 0,
                              2, ncmds, sizeofcmds, 0, 0)
        return bytearray(header + build_version + dylib_cmd)

    def test_list_dylib_dependencies_parses_load_dylib(self):
        data = self._build_macho_with_dependency()
        header = mp.parse_header(data)
        deps = list(mp.list_dylib_dependencies(data, header))
        self.assertEqual(len(deps), 1)
        dep = deps[0]
        self.assertEqual(dep["kind"], "required")
        self.assertEqual(dep["path"], "/System/Library/Frameworks/Foundation.framework/Foundation")
        self.assertEqual(dep["current_version"], "1.0.0")
        self.assertEqual(dep["compatibility_version"], "1.0.0")

    def test_list_dylib_dependencies_recognizes_weak_and_reexport(self):
        for kind_cmd, expected_kind in ((mp.LC_LOAD_WEAK_DYLIB, "weak"),
                                         (mp.LC_REEXPORT_DYLIB, "reexport"),
                                         (mp.LC_LOAD_UPWARD_DYLIB, "upward")):
            data = self._build_macho_with_dependency(kind_cmd=kind_cmd)
            header = mp.parse_header(data)
            deps = list(mp.list_dylib_dependencies(data, header))
            self.assertEqual(deps[0]["kind"], expected_kind)

    def test_redirect_dylib_dependency_rewrites_path_in_place(self):
        data = self._build_macho_with_dependency(
            path=b"/System/Library/Frameworks/AppKit.framework/AppKit")
        header = mp.parse_header(data)
        original_len = len(data)
        match = mp.redirect_dylib_dependency(
            data, header,
            "/System/Library/Frameworks/AppKit.framework/AppKit",
            "/S/L/F/UIKit.framework/UIKit")
        self.assertEqual(match["kind"], "required")
        self.assertEqual(len(data), original_len)
        deps = list(mp.list_dylib_dependencies(data, header))
        self.assertEqual(deps[0]["path"], "/S/L/F/UIKit.framework/UIKit")

    def test_redirect_dylib_dependency_rejects_path_too_long(self):
        data = self._build_macho_with_dependency(path=b"/a")
        header = mp.parse_header(data)
        with self.assertRaises(mp.MachOError):
            mp.redirect_dylib_dependency(
                data, header, "/a",
                "/a-considerably-longer-replacement-path-that-does-not-fit-in-the-slot")

    def test_redirect_dylib_dependency_rejects_unknown_path(self):
        data = self._build_macho_with_dependency()
        header = mp.parse_header(data)
        with self.assertRaises(mp.MachOError):
            mp.redirect_dylib_dependency(data, header, "/nonexistent", "/replacement")

    def test_classify_dependency_uses_built_in_table_without_sdk_path(self):
        status, _ = mp.classify_dependency(
            "/System/Library/Frameworks/Foundation.framework/Foundation")
        self.assertEqual(status, "available")
        status, _ = mp.classify_dependency(
            "/System/Library/Frameworks/AppKit.framework/AppKit")
        self.assertEqual(status, "unavailable")
        status, _ = mp.classify_dependency(
            "/System/Library/Frameworks/TotallyMadeUp.framework/TotallyMadeUp")
        self.assertEqual(status, "unknown")

    def test_classify_dependency_special_cases_libsystem_without_sdk_search(self):
        # Confirmed directly against a real iPhoneOS26.5 SDK (2026-09-06):
        # there is no discoverable .tbd for the umbrella libSystem.B.dylib
        # anywhere under usr/lib at all, so neither the path check nor the
        # .tbd-scanning fallback can ever find it -- this must be a
        # hardcoded special case, not something an SDK search would solve
        # given enough cleverness. Passing an ios_sdk_path that couldn't
        # possibly contain a match confirms the special case bypasses the
        # search entirely rather than coincidentally succeeding some other way.
        status, detail = mp.classify_dependency("/usr/lib/libSystem.B.dylib",
                                                   "/nonexistent/sdk/path")
        self.assertEqual(status, "available")
        self.assertIn("not resolved via SDK lookup", detail)

    def test_classify_dependency_prefers_sdk_path_when_given(self):
        sdk_dir = Path(self._get_tmp_dir())
        fw_dir = sdk_dir / "System/Library/Frameworks/Foundation.framework"
        fw_dir.mkdir(parents=True)
        (fw_dir / "Foundation").write_bytes(b"")
        status, detail = mp.classify_dependency(
            "/System/Library/Frameworks/Foundation.framework/Foundation", str(sdk_dir))
        self.assertEqual(status, "available")
        self.assertIn(str(sdk_dir), detail)

        status, _ = mp.classify_dependency(
            "/System/Library/Frameworks/NotThere.framework/NotThere", str(sdk_dir))
        self.assertEqual(status, "unavailable")

    def test_classify_dependency_finds_install_name_declared_by_a_relocated_tbd(self):
        # General case for the .tbd-scanning fallback: a real dylib whose
        # stub file lives under a different name than the install name it
        # declares -- ld resolves by the .tbd's own declared install-name,
        # not by where the stub file sits on disk. (libSystem.B.dylib hits
        # this same relocation but is special-cased separately -- see
        # test_classify_dependency_special_cases_libsystem_without_sdk_search
        # -- because a real SDK doesn't even have a .tbd for it under any
        # name, which this general mechanism can't and isn't meant to
        # solve; libFoo here stands in for an ordinary dylib that does have
        # a discoverable, just relocated, stub.)
        sdk_dir = Path(self._get_tmp_dir())
        lib_dir = sdk_dir / "usr/lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "libFoo_internal.tbd").write_text(
            '--- !tapi-tbd-v3\n'
            'archs: [ arm64 ]\n'
            'install-name: /usr/lib/libFoo.dylib\n'
            'exports: []\n'
        )
        status, detail = mp.classify_dependency("/usr/lib/libFoo.dylib", str(sdk_dir))
        self.assertEqual(status, "available")
        self.assertIn("declares this install name", detail)

    def test_classify_dependency_unavailable_when_no_file_and_no_tbd_matches(self):
        sdk_dir = Path(self._get_tmp_dir())
        (sdk_dir / "usr/lib").mkdir(parents=True)
        status, _ = mp.classify_dependency("/usr/lib/libTotallyMadeUp.dylib", str(sdk_dir))
        self.assertEqual(status, "unavailable")

    def test_read_tbd_install_name_ignores_non_tbd_files(self):
        tmp = Path(self._get_tmp_dir())
        real_looking = tmp / "not_a_stub"
        real_looking.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 60)  # MH_MAGIC_64-ish junk
        self.assertIsNone(mp._read_tbd_install_name(str(real_looking)))

    def _get_tmp_dir(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def _build_macho_with_symbols_and_deps(self):
        """Two dependencies (Foundation ordinal 1, AppKit ordinal 2) plus a
        symbol table with one undefined symbol pointing at each (via
        n_desc's library ordinal) and one locally defined symbol -- enough
        to test undefined_symbols_by_dependency actually correlates a
        symbol back to the dependency it came from, not just the raw
        symbol/dependency lists in isolation."""
        platform, minos, sdk, ntools = 1, 0x0B0000, 0x0B0000, 0
        build_version = struct.pack("<IIIIII", mp.LC_BUILD_VERSION, 24,
                                     platform, minos, sdk, ntools)

        def dylib_cmd(path):
            name = path + b"\x00"
            header_size = 24
            padded_size = (header_size + len(name) + 7) // 8 * 8
            cmd = struct.pack("<IIIIII", mp.LC_LOAD_DYLIB, padded_size, header_size, 0,
                               mp.encode_version("1.0.0"), mp.encode_version("1.0.0"))
            cmd += name
            cmd += b"\x00" * (padded_size - len(cmd))
            return cmd

        foundation_cmd = dylib_cmd(b"/System/Library/Frameworks/Foundation.framework/Foundation")
        appkit_cmd = dylib_cmd(b"/System/Library/Frameworks/AppKit.framework/AppKit")

        strtab = b"\x00undefined_from_foundation\x00undefined_from_appkit\x00defined_symbol\x00"
        off_foundation_sym = 1
        off_appkit_sym = off_foundation_sym + len(b"undefined_from_foundation\x00")
        off_defined_sym = off_appkit_sym + len(b"undefined_from_appkit\x00")

        N_EXT, N_SECT = 0x01, 0x0E
        symbols = b"".join([
            struct.pack("<IBBHQ", off_foundation_sym, N_EXT, 0, 1 << 8, 0),
            struct.pack("<IBBHQ", off_appkit_sym, N_EXT, 0, 2 << 8, 0),
            struct.pack("<IBBHQ", off_defined_sym, N_EXT | N_SECT, 0, 0, 0x1000),
        ])

        symtab_cmd = struct.pack("<IIIIII", mp.LC_SYMTAB, 24, 0, 0, 0, 0)  # placeholder offsets

        ncmds = 4
        cmds_no_symtab = build_version + foundation_cmd + appkit_cmd
        sizeofcmds = len(cmds_no_symtab) + len(symtab_cmd)
        header = struct.pack("<IiiIIIII", mp.MH_MAGIC_64, mp.CPU_TYPE_ARM64, 0,
                              2, ncmds, sizeofcmds, 0, 0)

        symoff = 32 + sizeofcmds
        stroff = symoff + len(symbols)
        symtab_cmd = struct.pack("<IIIIII", mp.LC_SYMTAB, 24, symoff, 3, stroff, len(strtab))

        return bytearray(header + cmds_no_symtab + symtab_cmd + symbols + strtab)

    def test_undefined_symbols_by_dependency_correlates_ordinals(self):
        data = self._build_macho_with_symbols_and_deps()
        header = mp.parse_header(data)
        by_dep = mp.undefined_symbols_by_dependency(data, header)
        self.assertEqual(
            by_dep,
            {
                "/System/Library/Frameworks/Foundation.framework/Foundation": ["undefined_from_foundation"],
                "/System/Library/Frameworks/AppKit.framework/AppKit": ["undefined_from_appkit"],
            },
        )


if __name__ == "__main__":
    unittest.main()
