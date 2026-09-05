// Minimal arm64 Darwin CLI test binary for the maciOS bring-up milestone.
//
// Deliberately avoids libSystem/dyld: it makes BSD syscalls directly via
// `svc #0x80`, so it links into a standalone Mach-O executable without a
// macOS SDK. This keeps the "smallest working milestone" test case free of
// any missing-framework problems -- it only exercises the platform tag /
// code signature question that tools/macho_patch.py addresses.
//
// BSD syscall numbers (arm64: x16 = number, positive, unlike Mach traps
// which use negative numbers): write = 4, exit = 1.

.section __TEXT,__text
.globl __start
.align 2

__start:
    mov x0, #1          // fd = STDOUT_FILENO
    adrp x1, msg@PAGE
    add x1, x1, msg@PAGEOFF
    mov x2, #31         // sizeof(msg) below
    mov x16, #4         // SYS_write
    svc #0x80

    mov x0, #0          // exit status
    mov x16, #1         // SYS_exit
    svc #0x80

.section __TEXT,__const
msg:
    .ascii "hello from native arm64 maciOS\n" // 31 bytes
