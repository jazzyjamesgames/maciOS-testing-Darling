import shutil
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "loader"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dump_structure as ds  # noqa: E402
from test_macho_patch import build_fixture, HAVE_TOOLCHAIN  # noqa: E402


@unittest.skipUnless(HAVE_TOOLCHAIN, "clang+lld not available to build a real Mach-O fixture")
class TestDumpStructure(unittest.TestCase):
    """Confirms dump_structure.py's svc-scan finds exactly what
    macho_loader.c's own C-side scan is supposed to find (same bitmask
    logic, independently reimplemented in C -- this is the Python-side
    cross-check, validated against a real compiled binary in this
    sandbox, since macho_loader.c itself can only be compiled and run on
    a real macOS toolchain -- see docs/custom-loader.md)."""

    def setUp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.binary = build_fixture(Path(d))

    def test_finds_both_svc_sites_in_hello_s(self):
        svc_sites = ds.dump(str(self.binary))
        self.assertEqual(len(svc_sites), 2)
        imms = {imm for _, imm in svc_sites}
        self.assertEqual(imms, {0x80})

    def test_is_svc_instruction_matches_known_encoding(self):
        # svc #0x80 -- confirmed against fixtures/hello.s's own compiled
        # bytes (word 0xd4001001) before being trusted anywhere else.
        matched, imm = ds.is_svc_instruction(0xd4001001)
        self.assertTrue(matched)
        self.assertEqual(imm, 0x80)

    def test_is_svc_instruction_rejects_non_svc(self):
        matched, _ = ds.is_svc_instruction(0xd2800020)  # mov x0, #1
        self.assertFalse(matched)


if __name__ == "__main__":
    unittest.main()
