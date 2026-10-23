#!/usr/bin/env bash
# Install everything MSR_Finder and its benchmark need, on a clean Ubuntu
# (tested: the TACAS 2027 VM, Ubuntu 26.04, amd64 and arm64). No root needed:
# everything goes under your home directory, in the places the tool looks by
# default.
#
#     bash install.sh                  the tool, HyperQB and the three baselines
#     bash install.sh --tool-only      the tool and HyperQB (enough for the smoke test)
#     bash install.sh --no-hyperqb     skip HyperQB (Rust build, ~5 min)
#     bash install.sh --no-bashrc      do not add env.sh to ~/.bashrc
#
# env.sh sets up a terminal for the tool. install.sh adds one marked line to
# ~/.bashrc that loads it in every new terminal; in the terminal you installed
# from, run  source env.sh  once (or open a new terminal).
#
# What goes where (all versions pinned; each step is skipped if already done):
#   ~/micromamba/bin/micromamba          micromamba 2.3.2 (conda package manager)
#   ~/micromamba/envs/spot               python 3.14 + spot 2.15.1 + graphviz + matplotlib
#   ~/micromamba/envs/hyperqb            libz3 4.16.0 + clang 22.1.8 (HyperQB's build)
#   ~/micromamba/envs/explainhyper       python 3.11 + spot 2.11.6 + ExplainHyper's packages
#   ~/.dotnet                            .NET 8 runtime (AutoHyper), .NET 7 SDK (CATS)
#   ~/.rustup, ~/.cargo                  Rust 1.97.1 (HyperQB's build only)
#   external_tools/HyperQB/...           the HyperQB binary
#   bench/tools/{CATS,CORP,ExplainHyper} the baselines, at pinned commits
#   .env/bin                             links to autfilt, ltl2tgba, ..., dot
#   env.sh                               the environment for the commands in README.md
#
# On arm64, conda-forge has no spot package, so spot is built from its release
# tarball into the same environments (~20 min each with 4 cores).
# The log of everything is in install.log.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG="$ROOT/install.log"
MAMBA_VERSION=2.3.2-0
SPOT_VERSION=2.15.1
EH_SPOT_VERSION=2.11.6
RUST_VERSION=1.97.1
HYPERQB_URL=https://github.com/HyperQB/HyperRUSTY.git
HYPERQB_REV=9496761c2b01a623408d1b3cd218236410914524
export MAMBA_ROOT_PREFIX="$HOME/micromamba"
MM="$MAMBA_ROOT_PREFIX/bin/micromamba"
SPOT_ENV="$MAMBA_ROOT_PREFIX/envs/spot"
HQ_ENV="$MAMBA_ROOT_PREFIX/envs/hyperqb"
EH_ENV="$MAMBA_ROOT_PREFIX/envs/explainhyper"
DOTNET="$HOME/.dotnet"

TOOL_ONLY=0; HYPERQB=1; BASHRC=1
for a in "$@"; do
    case "$a" in
        --tool-only) TOOL_ONLY=1 ;;
        --no-hyperqb) HYPERQB=0 ;;
        --no-bashrc) BASHRC=0 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option $a (see --help)"; exit 2 ;;
    esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; printf '\n== %s\n' "$*" >> "$LOG"; }
ok()   { printf '   \033[92mok\033[0m  %s\n' "$*"; }
die()  { printf '\033[91m%s\033[0m\n--- last lines of %s ---\n' "$*" "$LOG" >&2
         tail -25 "$LOG" >&2; exit 1; }
run()  { "$@" >> "$LOG" 2>&1 || die "failed: $*"; }

case "$(uname -m)" in
    x86_64)        ARCH=amd64; MARCH=linux-64 ;;
    aarch64|arm64) ARCH=arm64; MARCH=linux-aarch64 ;;
    *) die "unsupported architecture $(uname -m) (amd64 and arm64 are supported)" ;;
esac
: > "$LOG"
echo "MSR_Finder install, $(date), $(uname -sm), $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")" | tee -a "$LOG"
for c in curl git make g++ tar bzip2 patch timeout; do
    command -v "$c" >/dev/null || die "missing '$c': sudo apt-get install -y build-essential curl git bzip2"
done
# Under QEMU (arm64 emulation in Docker Desktop) Ubuntu 26.04's tar cannot
# extract: it uses openat2, which QEMU does not have. If so, a python stand-in
# for 'tar -x[z]f FILE -C DIR' goes first on PATH (spot's and .NET's tarballs).
mkdir -p "$ROOT/.build/tmp"
if ! ( cd "$ROOT/.build/tmp" && rm -rf t && mkdir t && echo x > t/f && tar -czf t.tgz t && rm -r t \
       && tar -xzf t.tgz && [ -f t/f ] ) >/dev/null 2>&1; then
    mkdir -p "$ROOT/.build/shim"
    cat > "$ROOT/.build/shim/tar" <<'EOF'
#!/usr/bin/env python3
import sys, tarfile
a, f, d = sys.argv[1:], None, "."
if not a or "x" not in a[0].lstrip("-"):
    sys.exit("tar stand-in: only extraction (tar -x[z]f FILE [-C DIR]) is supported")
i = 1
while i < len(a):
    if a[i] == "-C": d = a[i + 1]; i += 2
    elif f is None and "f" in a[0]: f = a[i]; i += 1
    else: sys.exit("tar stand-in: unsupported argument " + a[i])
tarfile.open(f).extractall(d, filter="tar")
EOF
    chmod +x "$ROOT/.build/shim/tar"
    export PATH="$ROOT/.build/shim:$PATH"
    echo "   (tar cannot extract here, as under QEMU emulation; using a python stand-in)" | tee -a "$LOG"
fi

# --------------------------------------------------------------------------- #
say "micromamba $MAMBA_VERSION"
if [ ! -x "$MM" ]; then
    mkdir -p "$(dirname "$MM")"
    run curl -fsSL -o "$MM" \
        "https://github.com/mamba-org/micromamba-releases/releases/download/$MAMBA_VERSION/micromamba-$MARCH"
    chmod +x "$MM"
fi
ok "$("$MM" --version)"

# Build spot from the release tarball into an existing environment (arm64).
build_spot() {   # <version> <env>
    local v=$1 env=$2 src="$ROOT/.build/spot-$1"
    mkdir -p "$ROOT/.build"
    [ -d "$src" ] || { run curl -fsSL -o "$src.tar.gz" "https://www.lrde.epita.fr/dload/spot/spot-$v.tar.gz"
                       run tar -xzf "$src.tar.gz" -C "$ROOT/.build"; }
    echo "   building spot $v from source (this is the long step on arm64)"
    ( cd "$src" && ./configure --prefix="$env" PYTHON="$env/bin/python3" --disable-devel \
          CPPFLAGS="-I$env/include" LDFLAGS="-L$env/lib -Wl,-rpath,$env/lib" \
      && make -j"$(nproc)" && make install ) >> "$LOG" 2>&1 || die "spot $v build failed"
}

say "spot environment (python 3.14, spot $SPOT_VERSION, graphviz, matplotlib)"
if ! "$SPOT_ENV/bin/python3" -c "import spot, buddy, matplotlib" 2>/dev/null; then
    if [ "$ARCH" = amd64 ]; then
        run "$MM" create -y -p "$SPOT_ENV" -c conda-forge \
            "python=3.14" "spot=$SPOT_VERSION" "graphviz=14.1.2" "matplotlib=3.11.1"
    else
        run "$MM" create -y -p "$SPOT_ENV" -c conda-forge \
            "python=3.14" "graphviz=14.1.2" "matplotlib=3.11.1"
        build_spot "$SPOT_VERSION" "$SPOT_ENV"
    fi
fi
"$SPOT_ENV/bin/python3" -c "import spot, buddy, matplotlib" 2>>"$LOG" || die "spot environment does not import spot"
ok "spot $("$SPOT_ENV/bin/python3" -c 'import spot; print(spot.version())')"

ldconfig -p 2>/dev/null | grep -q libicuuc || export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
say ".NET 8 runtime (AutoHyper)"
mkdir -p "$ROOT/.build"
[ -f "$ROOT/.build/dotnet-install.sh" ] || run curl -fsSL -o "$ROOT/.build/dotnet-install.sh" https://dot.net/v1/dotnet-install.sh
if ! ls "$DOTNET/shared/Microsoft.NETCore.App/" 2>/dev/null | grep -q '^8\.'; then
    run bash "$ROOT/.build/dotnet-install.sh" --channel 8.0 --runtime dotnet --install-dir "$DOTNET" --no-path
fi
ok "$(ls "$DOTNET/shared/Microsoft.NETCore.App/" | tr '\n' ' ')"

say "AutoHyper (shipped in external_tools/AutoHyper)"
AH="$ROOT/external_tools/AutoHyper/app"
printf '{\n    "autfilt": "%s",\n    "ltl2tgba": "%s"\n}\n' "$SPOT_ENV/bin/autfilt" "$SPOT_ENV/bin/ltl2tgba" > "$AH/paths.json"
if [ "$ARCH" = arm64 ] && [ "$(head -c 2 "$AH/AutoHyper")" != "#!" ]; then
    # The shipped launcher is an x86-64 apphost; AutoHyper.dll itself is
    # portable .NET, so on arm64 the launcher becomes a script that runs it.
    mv "$AH/AutoHyper" "$AH/AutoHyper.x86_64"
    printf '#!/bin/sh\nexec "${DOTNET_ROOT:-$HOME/.dotnet}/dotnet" "$(dirname "$0")/AutoHyper.dll" "$@"\n' > "$AH/AutoHyper"
fi
chmod +x "$AH/AutoHyper" "$ROOT/docker/smoke.sh" "$ROOT/smoke_test.sh" "$ROOT/bench/run.sh" \
         "$ROOT/bench/tools/fetch.sh" "$ROOT/bench/scripts/solver_wrappers/"* 2>/dev/null || true
DOTNET_ROOT="$DOTNET" "$AH/AutoHyper" --version >> "$LOG" 2>&1 || true
ok "paths.json -> $SPOT_ENV/bin"

# --------------------------------------------------------------------------- #
if [ "$HYPERQB" = 1 ]; then
    say "HyperQB 2.0 (Rust $RUST_VERSION, libz3 4.16.0) -- ~5 min"
    HQ_BIN="$ROOT/external_tools/HyperQB/target/release/HyperRUSTY"
    if [ ! -x "$HQ_BIN" ]; then
        [ -f "$HQ_ENV/lib/libz3.so" ] || \
            run "$MM" create -y -p "$HQ_ENV" -c conda-forge "libz3=4.16.0" "libclang=22.1.8" "clang=22.1.8"
        if [ ! -x "$HOME/.cargo/bin/rustup" ]; then
            run curl -fsSL -o "$ROOT/.build/rustup-init.sh" https://sh.rustup.rs
            run sh "$ROOT/.build/rustup-init.sh" -y --no-modify-path --profile minimal \
                --default-toolchain "$RUST_VERSION"
        fi
        run "$HOME/.cargo/bin/rustup" toolchain install "$RUST_VERSION" --profile minimal
        src="$ROOT/.build/HyperRUSTY"
        if [ ! -d "$src" ]; then
            run git init -q "$src"
            run git -C "$src" remote add origin "$HYPERQB_URL"
            run git -C "$src" fetch -q --depth 1 origin "$HYPERQB_REV"
            run git -C "$src" checkout -q FETCH_HEAD
        fi
        # bindgen (z3-sys) runs conda's libclang, which does not find its own
        # builtin headers (stdbool.h) unless told where they are
        res="$(ls -d "$HQ_ENV"/lib/clang/*/include | head -1)"
        ( cd "$src" && Z3_SYS_Z3_HEADER="$HQ_ENV/include/z3.h" LIBRARY_PATH="$HQ_ENV/lib" \
              LD_LIBRARY_PATH="$HQ_ENV/lib" LIBCLANG_PATH="$HQ_ENV/lib" \
              BINDGEN_EXTRA_CLANG_ARGS="-I$res" \
              "$HOME/.cargo/bin/cargo" "+$RUST_VERSION" build --release ) >> "$LOG" 2>&1 \
            || die "HyperQB build failed"
        mkdir -p "$(dirname "$HQ_BIN")"
        cp "$src/target/release/HyperRUSTY" "$HQ_BIN"
    fi
    ok "$HQ_BIN"
fi

# --------------------------------------------------------------------------- #
if [ "$TOOL_ONLY" = 0 ]; then
    say "baselines: .NET 7 SDK (to build CATS)"
    if ! ls "$DOTNET/sdk" 2>/dev/null | grep -q '^7\.'; then
        run bash "$ROOT/.build/dotnet-install.sh" --channel 7.0 --install-dir "$DOTNET" --no-path
    fi
    ok "$(ls "$DOTNET/sdk" | tr '\n' ' ')"

    say "baselines: ExplainHyper environment (python 3.11, spot $EH_SPOT_VERSION)"
    if ! "$EH_ENV/bin/python3" -c "import aiger, pysat, satispy, spot" 2>/dev/null; then
        if [ "$ARCH" = amd64 ]; then
            run "$MM" create -y -p "$EH_ENV" -c conda-forge \
                "python=3.11" "spot=$EH_SPOT_VERSION" "pygraphviz=1.14" "graphviz=14.1.2" pip
        else
            run "$MM" create -y -p "$EH_ENV" -c conda-forge "python=3.11" "pygraphviz=1.14" "graphviz=14.1.2" pip
            build_spot "$EH_SPOT_VERSION" "$EH_ENV"
        fi
        run "$EH_ENV/bin/python3" -m pip install --no-cache-dir \
            "py-aiger==8.1.0" "python-sat==1.9.dev15" "satispy==1.4" "numpy==2.4.6"
    fi
    "$EH_ENV/bin/python3" -c "import aiger, pysat, satispy, spot" 2>>"$LOG" || die "ExplainHyper environment incomplete"
    ok "$EH_ENV"

    say "baselines: CATS, CORP, ExplainHyper at pinned commits (bench/tools/fetch.sh)"
    for t in cats corp explainhyper; do
        PATH="$DOTNET:$PATH" DOTNET_ROOT="$DOTNET" DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 \
            bash "$ROOT/bench/tools/fetch.sh" "$t" >> "$LOG" 2>&1 || die "fetching $t failed"
    done
    ok "$(bash "$ROOT/bench/tools/fetch.sh" --list | tail -3 | awk '{printf "%s=%s ", $1, $3}')"
fi

# --------------------------------------------------------------------------- #
say "env.sh"
# .NET needs libicu unless told to run culture-invariant; AutoHyper and CATS
# format nothing culture-dependent, so invariant mode is safe where it is missing.
ICU_LINE=""
if ! ldconfig -p 2>/dev/null | grep -q libicuuc; then
    ICU_LINE="export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1   # no libicu on this system"
fi
mkdir -p "$ROOT/.env/bin"
for b in autfilt ltl2tgba ltlfilt ltlsynt randltl randaut dot; do
    [ -e "$SPOT_ENV/bin/$b" ] && ln -sf "$SPOT_ENV/bin/$b" "$ROOT/.env/bin/$b"
done
cat > "$ROOT/env.sh" <<EOF
# written by install.sh -- sets up a terminal for the tool:  source env.sh
export MSR_FINDER="$ROOT"
export DOTNET_ROOT="$DOTNET"
export SPOT_ENV_PY="$SPOT_ENV/bin/python3"
export EXPLAINHYPER_ENV_PY="$EH_ENV/bin/python3"
export HYPERQB_LIB="$HQ_ENV/lib"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
$ICU_LINE
case ":\$PATH:" in *":$ROOT/.env/bin:"*) ;; *) export PATH="$ROOT/.env/bin:$DOTNET:\$PATH" ;; esac
EOF
ok "$ROOT/env.sh"

if [ "$BASHRC" = 1 ]; then
    say "~/.bashrc: load env.sh in every new terminal"
    # one marked line; it does nothing once this directory is gone
    line="[ -f \"$ROOT/env.sh\" ] && . \"$ROOT/env.sh\"   # MSR_Finder artifact, added by install.sh"
    grep -qF "$ROOT/env.sh" "$HOME/.bashrc" 2>/dev/null || echo "$line" >> "$HOME/.bashrc"
    ok "to undo: delete the line ending in 'added by install.sh' from ~/.bashrc"
fi

echo
printf '\033[92mdone.\033[0m  Next (in this terminal; new terminals load env.sh by themselves):\n    source env.sh\n    ./smoke_test.sh\n'
