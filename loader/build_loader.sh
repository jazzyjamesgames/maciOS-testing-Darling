#!/bin/sh
# Builds loader/macho_loader -- a normal macOS command-line tool (no
# special target/nostdlib flags: unlike loader/target, this is not the
# thing being loaded, it's the loader itself, and needs the real libSystem/
# Foundation like any ordinary tool). Only buildable on a real macOS
# toolchain (needs <mach-o/loader.h>, <libkern/OSCacheControl.h>) -- see
# docs/custom-loader.md.
set -e
cd "$(dirname "$0")"

clang -Wall -Wextra -o macho_loader macho_loader.c
echo "built loader/macho_loader"
