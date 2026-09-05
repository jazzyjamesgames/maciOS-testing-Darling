#!/bin/sh
# Cross-compiles fixtures/hello.s into a thin arm64 macOS Mach-O executable
# without needing a Mac or the macOS SDK. Requires clang with an
# ld64.lld-capable lld (Ubuntu/Debian: apt install clang lld).
set -e
cd "$(dirname "$0")"
clang -target arm64-apple-macos11 -fuse-ld=lld -nostdlib \
    -Wl,-e,__start -Wl,-platform_version,macos,11.0,11.0 \
    hello.s -o hello_macos_arm64
echo "built fixtures/hello_macos_arm64"
