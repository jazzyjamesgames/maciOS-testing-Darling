// A custom Mach-O loader that does NOT use dyld or macOS's normal exec()
// path at all: it reads a static, no-libSystem arm64 Mach-O executable
// (fixtures/hello.s, built by build_target.sh) directly off disk, maps its
// __TEXT segment into freshly-reserved memory in THIS process, resolves
// its entry point from LC_MAIN, statically scans the mapped code for `svc`
// instructions (documenting where the target's syscalls are, without
// intercepting them yet -- see docs/custom-loader.md and
// docs/syscall-table-diff.md for why that's next, not now), and then
// actually jumps to the entry point.
//
// This is Milestone 1 of the custom-loader track: prove the parser/mapper
// logic is correct by observing CORRECT BEHAVIOR when we hand off
// execution ourselves -- the target's own `write` syscall should print its
// message on THIS process's real stdout, and its own `exit` syscall should
// end THIS process with status 0, both of which hit the real kernel
// directly (nothing here intercepts or redirects them yet; that's
// deliberately future, on-device work). A working run of this loader,
// producing that exact output, is the correctness proof -- not a separate
// test harness pretending to check its internals.
//
// Deliberately runs as a normal macOS command-line tool (built by and for
// the real macOS CI runner), not cross-compiled for iOS: proving the
// parser/loader logic is correct comes first, and can be done today,
// before any of iOS's own JIT/sandbox restrictions on writable+executable
// memory are even relevant. See MILESTONES.md for what's flagged as
// on-device follow-up.
#include <fcntl.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <libkern/OSCacheControl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef LC_REQ_DYLD
#define LC_REQ_DYLD 0x80000000
#endif
#ifndef LC_MAIN
#define LC_MAIN (0x28 | LC_REQ_DYLD)
#endif

// struct entry_point_command isn't always present under that exact name
// across SDK versions, but its layout (cmd, cmdsize, entryoff, stacksize)
// has been stable since LC_MAIN was introduced -- declared locally so this
// doesn't depend on a specific SDK's naming.
struct macios_entry_point_command {
  uint32_t cmd;
  uint32_t cmdsize;
  uint64_t entryoff;
  uint64_t stacksize;
};

// ARM64 SVC encoding (ARMv8 ARM, "Exception generation" class):
// bits[31:21] = 0b11010100000, bits[20:5] = imm16, bits[4:0] = 0b00001.
// `svc #0` is the well-known bytes 01 00 00 D4; `svc #0x80` (Darwin's BSD
// syscall trap immediate) is 01 10 00 D4 -- confirmed directly against
// fixtures/hello.s's own compiled bytes before writing this, not assumed
// (see docs/custom-loader.md).
static int is_svc_instruction(uint32_t word, uint16_t *out_imm) {
  if ((word & 0xFFE0001Fu) == 0xD4000001u) {
    *out_imm = (uint16_t)((word >> 5) & 0xFFFFu);
    return 1;
  }
  return 0;
}

int main(int argc, char **argv) {
  if (argc != 2) {
    fprintf(stderr, "usage: %s <arm64-macho-binary>\n", argv[0]);
    return 64;
  }
  const char *path = argv[1];

  int fd = open(path, O_RDONLY);
  if (fd < 0) {
    perror("open");
    return 1;
  }
  struct stat st;
  if (fstat(fd, &st) != 0) {
    perror("fstat");
    close(fd);
    return 1;
  }
  size_t file_size = (size_t)st.st_size;

  // Read the whole file into this loader's own heap for parsing -- kept
  // entirely separate from the memory we reserve below for the TARGET's
  // segments, mirroring the real distinction between "the loader's own
  // address space" and "the address space it's constructing for what it
  // loads".
  uint8_t *file_data = malloc(file_size);
  if (!file_data) {
    perror("malloc");
    close(fd);
    return 1;
  }
  ssize_t nread = read(fd, file_data, file_size);
  close(fd);
  if (nread < 0 || (size_t)nread != file_size) {
    perror("read");
    return 1;
  }

  if (file_size < sizeof(struct mach_header_64)) {
    fprintf(stderr, "file too small to be a Mach-O\n");
    return 1;
  }
  struct mach_header_64 *hdr = (struct mach_header_64 *)file_data;
  if (hdr->magic != MH_MAGIC_64) {
    fprintf(stderr, "not a 64-bit Mach-O (magic=%#x)\n", hdr->magic);
    return 1;
  }
  if (hdr->cputype != CPU_TYPE_ARM64) {
    fprintf(stderr, "cputype=%#x is not CPU_TYPE_ARM64 -- this loader is arm64-only\n",
            hdr->cputype);
    return 1;
  }

  printf("mach_header_64: cputype=%#x cpusubtype=%#x filetype=%u ncmds=%u "
         "sizeofcmds=%u flags=%#x\n",
         (unsigned)hdr->cputype, (unsigned)hdr->cpusubtype, hdr->filetype, hdr->ncmds,
         hdr->sizeofcmds, hdr->flags);

  // Pass 1: walk load commands. Record every LC_SEGMENT_64 (skipping
  // __PAGEZERO -- a real reservation-only hole with maxprot/initprot both
  // VM_PROT_NONE and zero filesize, never meant to be backed by real
  // memory; a real loader would still reserve that address range to catch
  // null-pointer accesses, but that's not this milestone's concern), find
  // the __TEXT segment specifically (LC_MAIN's entryoff is relative to
  // it), and find LC_MAIN's entryoff.
  uint64_t lowest_vmaddr = UINT64_MAX;
  uint64_t highest_vmend = 0;
  uint64_t text_vmaddr = 0;
  int have_text = 0;
  uint64_t entryoff = 0;
  int have_entry = 0;

  uint8_t *cmd_ptr = file_data + sizeof(struct mach_header_64);
  for (uint32_t i = 0; i < hdr->ncmds; i++) {
    struct load_command *lc = (struct load_command *)cmd_ptr;
    if (lc->cmd == LC_SEGMENT_64) {
      struct segment_command_64 *seg = (struct segment_command_64 *)cmd_ptr;
      printf("LC_SEGMENT_64 %-12s vmaddr=%#llx vmsize=%#llx fileoff=%#llx "
             "filesize=%#llx maxprot=%d initprot=%d nsects=%u\n",
             seg->segname, seg->vmaddr, seg->vmsize, seg->fileoff, seg->filesize,
             seg->maxprot, seg->initprot, seg->nsects);
      if (strcmp(seg->segname, "__PAGEZERO") == 0 || seg->vmsize == 0) {
        cmd_ptr += lc->cmdsize;
        continue;
      }
      if (seg->vmaddr < lowest_vmaddr) lowest_vmaddr = seg->vmaddr;
      if (seg->vmaddr + seg->vmsize > highest_vmend) highest_vmend = seg->vmaddr + seg->vmsize;
      if (strcmp(seg->segname, "__TEXT") == 0) {
        text_vmaddr = seg->vmaddr;
        have_text = 1;
      }
    } else if (lc->cmd == LC_MAIN) {
      struct macios_entry_point_command *ep = (struct macios_entry_point_command *)cmd_ptr;
      entryoff = ep->entryoff;
      have_entry = 1;
      printf("LC_MAIN entryoff=%#llx stacksize=%#llx\n", ep->entryoff, ep->stacksize);
    }
    cmd_ptr += lc->cmdsize;
  }

  if (!have_text) {
    fprintf(stderr, "no __TEXT segment found\n");
    return 1;
  }
  if (!have_entry) {
    fprintf(stderr, "no LC_MAIN found -- this loader doesn't handle "
                     "LC_UNIXTHREAD-style entry points\n");
    return 1;
  }
  if (lowest_vmaddr == UINT64_MAX) {
    fprintf(stderr, "no mappable segments found\n");
    return 1;
  }

  size_t image_size = (size_t)(highest_vmend - lowest_vmaddr);
  printf("reserving %zu bytes for the image (vmaddr range %#llx..%#llx)\n",
         image_size, lowest_vmaddr, highest_vmend);

  // Reserve one contiguous, writable region for the whole image and let
  // the OS place it -- this binary is PIE (confirmed: `file` reports the
  // PIE flag on fixtures/hello.s's build), so its vmaddrs are Apple's
  // *preferred* load addresses, not ones we're obligated to honor
  // literally. `slide` below is this loader's own equivalent of what
  // dyld's ASLR would compute -- same idea, just computed by us instead.
  void *reserved = mmap(NULL, image_size, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (reserved == MAP_FAILED) {
    perror("mmap (reserve)");
    return 1;
  }
  int64_t slide = (int64_t)(uintptr_t)reserved - (int64_t)lowest_vmaddr;
  printf("reserved image base=%p, slide=%#llx\n", reserved,
         (unsigned long long)slide);

  // Pass 2: copy each segment's file contents to (slide + vmaddr). Anon
  // mmap is already zero-filled, so vmsize-beyond-filesize bytes (bss-like
  // padding) need no extra work.
  cmd_ptr = file_data + sizeof(struct mach_header_64);
  for (uint32_t i = 0; i < hdr->ncmds; i++) {
    struct load_command *lc = (struct load_command *)cmd_ptr;
    if (lc->cmd == LC_SEGMENT_64) {
      struct segment_command_64 *seg = (struct segment_command_64 *)cmd_ptr;
      if (strcmp(seg->segname, "__PAGEZERO") == 0 || seg->vmsize == 0) {
        cmd_ptr += lc->cmdsize;
        continue;
      }
      uint8_t *dest = (uint8_t *)(uintptr_t)((int64_t)seg->vmaddr + slide);
      if (seg->filesize > 0) {
        memcpy(dest, file_data + seg->fileoff, (size_t)seg->filesize);
      }
      printf("mapped %-12s at %p (%llu bytes copied from file)\n", seg->segname,
             (void *)dest, seg->filesize);
    }
    cmd_ptr += lc->cmdsize;
  }

  // Statically scan the mapped __TEXT segment for `svc` instructions --
  // groundwork for a future JIT-based patch/redirect once this runs
  // on-device (see docs/syscall-table-diff.md), not interception yet:
  // this only records where they are, before we ever hand off execution.
  cmd_ptr = file_data + sizeof(struct mach_header_64);
  for (uint32_t i = 0; i < hdr->ncmds; i++) {
    struct load_command *lc = (struct load_command *)cmd_ptr;
    if (lc->cmd == LC_SEGMENT_64) {
      struct segment_command_64 *seg = (struct segment_command_64 *)cmd_ptr;
      if (strcmp(seg->segname, "__TEXT") == 0) {
        uint32_t *code = (uint32_t *)(uintptr_t)((int64_t)seg->vmaddr + slide);
        uint64_t nwords = seg->vmsize / 4;
        for (uint64_t w = 0; w < nwords; w++) {
          uint16_t imm = 0;
          if (is_svc_instruction(code[w], &imm)) {
            uint64_t vmaddr_here = seg->vmaddr + w * 4;
            printf("svc found: vmaddr=%#llx imm=%#x (mapped at %p)\n", vmaddr_here,
                   imm, (void *)&code[w]);
          }
        }
      }
    }
    cmd_ptr += lc->cmdsize;
  }

  // Now make __TEXT executable (and stop it being writable -- W^X). Must
  // invalidate the instruction cache first: on arm64, the data and
  // instruction caches are not coherent for freshly-written code the way
  // x86 automatically is, so without this a fetch from these addresses
  // could execute stale/garbage cache lines rather than the bytes we just
  // memcpy'd in. sys_icache_invalidate is the real Darwin API for this
  // (used by every JIT compiler on Apple platforms for the same reason).
  cmd_ptr = file_data + sizeof(struct mach_header_64);
  for (uint32_t i = 0; i < hdr->ncmds; i++) {
    struct load_command *lc = (struct load_command *)cmd_ptr;
    if (lc->cmd == LC_SEGMENT_64) {
      struct segment_command_64 *seg = (struct segment_command_64 *)cmd_ptr;
      if (strcmp(seg->segname, "__PAGEZERO") == 0 || seg->vmsize == 0) {
        cmd_ptr += lc->cmdsize;
        continue;
      }
      void *seg_addr = (void *)(uintptr_t)((int64_t)seg->vmaddr + slide);
      if (seg->initprot & VM_PROT_EXECUTE) {
        sys_icache_invalidate(seg_addr, (size_t)seg->vmsize);
      }
      // vm_prot_t's bit values (READ=1, WRITE=2, EXECUTE=4) are the same
      // numeric values as PROT_READ/PROT_WRITE/PROT_EXEC on Darwin, so
      // this cast is exact, not an approximation.
      if (mprotect(seg_addr, (size_t)seg->vmsize, seg->initprot) != 0) {
        perror("mprotect");
        return 1;
      }
    }
    cmd_ptr += lc->cmdsize;
  }

  // Called as a plain void(void) function, not given a real process
  // entry's initial register/stack state (argc/argv/envp/apple-vector in
  // x0-x3, a fresh stack) the way the kernel would set up for a real
  // execve(). This is deliberately fine ONLY because fixtures/hello.s's
  // _start never reads its incoming registers as arguments at all -- its
  // first instruction (`mov x0, #1`) immediately overwrites x0 for its own
  // write() call. A future target that actually expects argc/argv would
  // need this loader extended to fake up that initial state first.
  uint64_t entry_vmaddr = text_vmaddr + entryoff;
  void (*entry)(void) = (void (*)(void))(uintptr_t)((int64_t)entry_vmaddr + slide);
  printf("jumping to entry point at %p (vmaddr=%#llx) -- if this loader's "
         "parsing/mapping is correct, the target's own real syscalls run "
         "next, unintercepted\n",
         (void *)entry, entry_vmaddr);
  fflush(stdout);

  entry();

  // Unreachable in practice: fixtures/hello.s's _start ends with its own
  // `exit` syscall, which terminates this whole process directly. Getting
  // here at all would itself be a correctness failure worth reporting.
  fprintf(stderr, "entry point returned control to the loader -- unexpected\n");
  return 1;
}
