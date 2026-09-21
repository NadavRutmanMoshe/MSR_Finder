# MSR_Finder — the tool.
#
#     docker build -t msr-finder .
#     docker run --rm -it msr-finder                     # menu on example_1
#     docker run --rm msr-finder python3 tracemsr.py ../examples/example_1.txt --run all
#
# The build ends with docker/smoke.sh, so an image that builds is an image whose
# solver, spot bindings and graphviz all actually work.
#
# Build args:
#     WITH_HYPERQB=1    also build HyperQB 2.0 (Rust + Z3) for `--hyperQB`.
#                       Off by default: it adds a Rust toolchain stage and ~4
#                       minutes (108 crates), and the backend is optional --
#                       AutoHyper answers everything without it. When it is on,
#                       smoke.sh checks it too.
#     SPOT_VERSION / PY_VERSION / DOTNET_CHANNEL
#                       pinned to what produced the numbers in bench/results.
ARG WITH_HYPERQB=0

# --------------------------------------------------------------------------- #
# HyperQB (optional). Two stages with the same interface; the ARG picks one.
# --------------------------------------------------------------------------- #
FROM rust:1-bookworm AS hq-1
ARG HYPERQB_URL=https://github.com/HyperQB/HyperRUSTY.git
ARG HYPERQB_REV=9496761c2b01a623408d1b3cd218236410914524
ARG Z3_VERSION=4.16.0
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates bzip2 clang libclang-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /usr/local bin/micromamba
# The same Z3 the measured host linked against, from the same channel. The
# package is `libz3` -- it carries both include/z3.h and lib/libz3.so, which is
# all the z3-sys build needs; conda-forge has no package called `z3`.
RUN micromamba create -y -p /root/micromamba/envs/hyperqb -c conda-forge \
        "libz3=${Z3_VERSION}" \
    && micromamba clean -a -y
ENV Z3_SYS_Z3_HEADER=/root/micromamba/envs/hyperqb/include/z3.h \
    LIBRARY_PATH=/root/micromamba/envs/hyperqb/lib \
    LD_LIBRARY_PATH=/root/micromamba/envs/hyperqb/lib
RUN git clone "${HYPERQB_URL}" /src/hyperqb \
    && git -C /src/hyperqb checkout "${HYPERQB_REV}" \
    && cd /src/hyperqb && cargo build --release \
    && mkdir -p /out && cp target/release/HyperRUSTY /out/

FROM debian:bookworm-slim AS hq-0
RUN mkdir -p /out /root/micromamba/envs/hyperqb/lib

FROM hq-${WITH_HYPERQB} AS hyperqb

# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #
FROM debian:bookworm-slim

ARG SPOT_VERSION=2.15.1
ARG PY_VERSION=3.14
ARG DOTNET_CHANNEL=8.0
ARG WITH_HYPERQB=0

# python3 here is Debian's: the pipelines are stdlib-only and run on the system
# interpreter, exactly as on a host install. spot lives in its own environment
# and is reached through $SPOT_ENV_PY.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl bzip2 git patch procps python3 libicu72 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /usr/local bin/micromamba

# spot (+ its Python bindings) and graphviz, pinned. Same layout as the
# micromamba env the README's Setup section describes, so every default path in
# the code resolves without patching.
ENV MAMBA_ROOT_PREFIX=/root/micromamba
RUN micromamba create -y -p /root/micromamba/envs/spot -c conda-forge \
        "python=${PY_VERSION}" "spot=${SPOT_VERSION}" graphviz \
    && micromamba clean -a -y

# autfilt, ltl2tgba and dot are looked up on PATH by the MTSR pipeline and by
# the benchmark generators. Symlink them rather than putting the env on PATH:
# that would shadow the system python3 the tool itself runs on.
RUN for b in autfilt ltl2tgba ltlfilt ltlsynt randltl randaut dot; do \
        ln -sf "/root/micromamba/envs/spot/bin/$b" "/usr/local/bin/$b"; \
    done

# .NET runtime for AutoHyper (a .NET apphost). ~/.dotnet is where the pipelines
# look when there is no system-wide install, so DOTNET_ROOT is picked up for
# free -- it is still set explicitly here for anything run by hand.
RUN curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel "${DOTNET_CHANNEL}" --runtime dotnet \
         --install-dir /root/.dotnet --no-path \
    && rm /tmp/dotnet-install.sh
ENV DOTNET_ROOT=/root/.dotnet \
    PATH=/root/.dotnet:$PATH \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

ENV SPOT_ENV_PY=/root/micromamba/envs/spot/bin/python3

WORKDIR /opt/msr_finder
COPY . /opt/msr_finder

# HyperQB, when it was built above (an empty copy otherwise). The tool finds it
# at this path, and its libz3 through $HYPERQB_LIB.
COPY --from=hyperqb /out/ /opt/msr_finder/external_tools/HyperQB/target/release/
COPY --from=hyperqb /root/micromamba/envs/hyperqb/lib/ /root/micromamba/envs/hyperqb/lib/
ENV HYPERQB_LIB=/root/micromamba/envs/hyperqb/lib

# AutoHyper ships as a binary; git does not always carry the +x bit. Its
# paths.json is absolute by design, so it is rewritten for this image.
RUN chmod +x external_tools/AutoHyper/app/AutoHyper \
    && chmod +x docker/smoke.sh bench/run.sh bench/tools/fetch.sh \
                bench/scripts/solver_wrappers/* 2>/dev/null || true
RUN printf '{\n    "autfilt": "/root/micromamba/envs/spot/bin/autfilt",\n    "ltl2tgba": "/root/micromamba/envs/spot/bin/ltl2tgba"\n}\n' \
        > external_tools/AutoHyper/app/paths.json

# The build is its own test.
RUN /opt/msr_finder/docker/smoke.sh

WORKDIR /opt/msr_finder/scripts
CMD ["python3", "tracemsr.py", "../examples/example_1.txt"]
