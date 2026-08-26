#!/usr/bin/env bash
# Builds and installs libdatachannel (no media, no websocket) from the
# third_party/libdatachannel submodule.
# Usage: build-libdatachannel.sh --prefix=/usr/local [--static]
# --static builds libdatachannel.a, libjuice.a and libusrsctp.a (position
# independent, so PIE binaries can link them) instead of shared libraries.
set -euo pipefail

for arg in "$@"; do
    case "$arg" in
        --prefix=*) prefix="${arg#*=}" ;;
        --static) static=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 1 ;;
    esac
done
: "${prefix:?--prefix=DIR is required}"

src="$(cd "$(dirname "$0")/../../.." && pwd)/third_party/libdatachannel"
# NO_MEDIA and NO_EXAMPLES leave deps/libsrtp and deps/json unused, so only
# these three nested submodules have to be checked out.
for dep in libjuice usrsctp plog; do
    if [ ! -f "$src/deps/$dep/CMakeLists.txt" ]; then
        echo "$src/deps/$dep is empty: run" >&2
        echo "  git submodule update --init --recursive third_party/libdatachannel" >&2
        exit 1
    fi
done

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
cmake -S "$src" -B "$build" -DCMAKE_BUILD_TYPE=Release -DNO_MEDIA=1 -DNO_WEBSOCKET=1 \
    -DNO_EXAMPLES=1 -DNO_TESTS=1 -DCMAKE_INSTALL_PREFIX="$prefix" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    ${static:+-DBUILD_SHARED_LIBS=0 -DCMAKE_POSITION_INDEPENDENT_CODE=ON}
cmake --build "$build" -j"$(nproc)"
cmake --install "$build"
