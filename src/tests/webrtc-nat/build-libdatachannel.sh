#!/usr/bin/env bash
# Builds and installs libdatachannel (no media, no websocket) at a pinned commit.
# Usage: build-libdatachannel.sh --commit=SHA --prefix=/usr/local [--static]
# --static builds libdatachannel.a, libjuice.a and libusrsctp.a (position
# independent, so PIE binaries can link them) instead of shared libraries.
set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        --commit=*) commit="${arg#*=}" ;;
        --prefix=*) prefix="${arg#*=}" ;;
        --static) static=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 1 ;;
    esac
done
: "${commit:?--commit=SHA is required}"
: "${prefix:?--prefix=DIR is required}"

src=/tmp/libdatachannel
git init -q "$src"
cd "$src"
git remote add origin https://github.com/paullouisageneau/libdatachannel.git
# A shallow fetch of a single commit; submodules (libjuice, usrsctp, plog) are
# shallow too. That keeps the build-stage download small.
git fetch -q --depth 1 origin "$commit"
git checkout -q FETCH_HEAD
git submodule update -q --init --depth 1 --recursive

cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DNO_MEDIA=1 -DNO_WEBSOCKET=1 \
    -DNO_EXAMPLES=1 -DNO_TESTS=1 -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    ${static:+-DBUILD_SHARED_LIBS=0 -DCMAKE_POSITION_INDEPENDENT_CODE=ON}
cmake --build build -j"$(nproc)"
cmake --install build
rm -rf "$src"
