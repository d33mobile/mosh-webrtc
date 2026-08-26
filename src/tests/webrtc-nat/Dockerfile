# One image for both e2e roles (ssh server with mosh-server, mosh client).
# Build context is the repository root: docker build -f src/tests/webrtc-nat/Dockerfile .

###############################################################################
# libdatachannel: dependency for the mosh build and (as a shared lib) runtime.
FROM debian:trixie-slim AS libdatachannel
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Built from the vendored submodule (checked out sources, no git needed).
WORKDIR /src
COPY third_party/libdatachannel third_party/libdatachannel
COPY src/tests/webrtc-nat/build-libdatachannel.sh src/tests/webrtc-nat/
RUN src/tests/webrtc-nat/build-libdatachannel.sh --prefix=/usr/local

###############################################################################
# mosh: build with --enable-webrtc from the repository sources.
FROM libdatachannel AS mosh
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake pkg-config protobuf-compiler libprotobuf-dev \
        libncurses-dev zlib1g-dev perl \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN ./autogen.sh && ./configure --enable-webrtc && make -j"$(nproc)" && make install

###############################################################################
# runtime: shared for the server (sshd) and the client (mosh.pl + test driver).
FROM debian:trixie-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libprotobuf32t64 libncursesw6 libssl3t64 zlib1g perl iproute2 \
        openssh-server openssh-client python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd && ssh-keygen -A
COPY --from=libdatachannel /usr/local/lib/libdatachannel.so* /usr/local/lib/
COPY --from=mosh /usr/local/bin/mosh /usr/local/bin/mosh-server /usr/local/bin/mosh-client /usr/local/bin/
RUN ldconfig

# Throwaway key pair for the "mosh" test account; the same image serves both
# roles, so the private key at /key is present on the server too.
RUN useradd --create-home --shell /bin/sh mosh \
    && ssh-keygen -t ed25519 -N '' -f /key \
    && install -d -m 700 -o mosh -g mosh /home/mosh/.ssh \
    && install -m 600 -o mosh -g mosh /key.pub /home/mosh/.ssh/authorized_keys

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
COPY src/tests/webrtc-nat/entrypoint.sh /entrypoint.sh
COPY src/tests/webrtc-local-smoke.py src/tests/webrtc-ssh-smoke.py src/tests/webrtc-nat-e2e.py /tests/
ENTRYPOINT ["/entrypoint.sh"]
