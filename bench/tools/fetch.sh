#!/usr/bin/env bash
# Fetch the three baseline tools this benchmark compares against.
#
#     ./fetch.sh                  all three
#     ./fetch.sh corp cats        only those
#     ./fetch.sh --list           the pins, and what is already here
#
# They are NOT vendored in this repository. Each is someone else's tool, each
# carries its own license, and pinning a commit says exactly what was measured
# better than a copied directory does. Every pin below is the commit the numbers
# in `results/` were produced with.
#
#     CATS           cats-tool/cats                         GPL-3.0
#     CORP           reactive-systems/corp                  GPL-3.0
#     ExplainHyper   reactive-systems/                      MIT
#                      explaining-hyperproperty-violations
#
# ExplainHyper is patched (three diffs in patches/, see tools/README.md); the
# other two are used exactly as published.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BENCH="$(dirname "$HERE")"

CATS_URL=https://github.com/cats-tool/cats.git
CATS_REV=6096d741449e084a792af00c28bd9bbea0cb55ea

CORP_URL=https://github.com/reactive-systems/corp.git
CORP_REV=762c171af4e97b3bb6fa6a0af0a26f4b498ed372

EH_URL=https://github.com/reactive-systems/explaining-hyperproperty-violations.git
EH_REV=9f894a098df2677b6a78e29be4ec4acb7f57038d

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[93m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[91m%s\033[0m\n' "$*" >&2; exit 1; }

# A fresh clone at an exact commit, without pulling the whole history.
clone_at() {                       # url rev dest [--recursive]
    local url=$1 rev=$2 dest=$3 recurse=${4:-}
    if [ -e "$dest" ]; then
        warn "$(basename "$dest") is already here; delete it to re-fetch."
        return 1
    fi
    mkdir -p "$dest"
    git -C "$dest" init -q
    git -C "$dest" remote add origin "$url"
    git -C "$dest" fetch -q --depth 1 origin "$rev"
    git -C "$dest" checkout -q FETCH_HEAD
    if [ -n "$recurse" ]; then
        git -C "$dest" submodule -q update --init --recursive --depth 1
    fi
    return 0
}

fetch_corp() {
    say "CORP  ($CORP_REV)"
    clone_at "$CORP_URL" "$CORP_REV" "$HERE/CORP" || return 0
    rm -rf "$HERE/CORP/.git"
    [ -f "$HERE/CORP/corp.py" ] || die "CORP: corp.py missing after fetch"
    echo "  -> tools/CORP  (runs on the spot env python, no build)"
}

fetch_explainhyper() {
    say "ExplainHyper  ($EH_REV)"
    clone_at "$EH_URL" "$EH_REV" "$HERE/ExplainHyper" || return 0
    rm -rf "$HERE/ExplainHyper/.git"
    for p in altautomaton-get_label parser-traceProperty explain_hyper-traceProperty; do
        ( cd "$HERE/ExplainHyper" && patch -p0 --batch --forward < "$HERE/patches/$p.patch" ) \
            || die "ExplainHyper: $p.patch did not apply"
        echo "  applied $p.patch"
    done
    echo "  -> tools/ExplainHyper  (runs on \$EXPLAINHYPER_ENV_PY, no build)"
}

fetch_cats() {
    say "CATS  ($CATS_REV)"
    command -v dotnet >/dev/null 2>&1 || [ -x "$HOME/.dotnet/dotnet" ] \
        || die "CATS needs the .NET SDK (7.0; CATS targets net7.0). See tools/README.md."
    local dotnet_bin=dotnet
    command -v dotnet >/dev/null 2>&1 || dotnet_bin="$HOME/.dotnet/dotnet"

    local src="$HERE/.cats-src"
    rm -rf "$src"
    clone_at "$CATS_URL" "$CATS_REV" "$src" --recursive || return 0

    mkdir -p "$HERE/CATS"
    ( cd "$src/src/CATS" && "$dotnet_bin" build -c release -o "$HERE/CATS/app" )
    cp -r "$src/examples" "$HERE/CATS/examples"
    # Their already-synthesized SYNTCOMP systems (24 MB). This is the input the
    # SYNTCOMP corpus is built from, so keeping it makes ../gen/ self-contained.
    mkdir -p "$HERE/CATS/eval"
    cp "$src/eval/syntcomp_instances.json" "$HERE/CATS/eval/syntcomp_instances.json"
    rm -rf "$src"

    # CATS resolves spot through paths.json. It is pointed at our resource-capped
    # wrappers, not at spot itself: CATS waits on its spot children with no
    # timeout and no memory cap (see scripts/solver_wrappers/autfilt).
    cat > "$HERE/CATS/app/paths.json" <<JSON
{
    "autfilt": "$BENCH/scripts/solver_wrappers/autfilt",
    "ltl2tgba": "$BENCH/scripts/solver_wrappers/ltl2tgba"
}
JSON
    [ -x "$HERE/CATS/app/CATS" ] || die "CATS: app/CATS missing after build"
    echo "  -> tools/CATS/app/CATS  (paths.json -> scripts/solver_wrappers/)"
}

list() {
    printf '%-14s %-44s %s\n' TOOL PIN PRESENT
    printf '%-14s %-44s %s\n' CATS         "$CATS_REV" "$([ -x "$HERE/CATS/app/CATS" ] && echo yes || echo no)"
    printf '%-14s %-44s %s\n' CORP         "$CORP_REV" "$([ -f "$HERE/CORP/corp.py" ] && echo yes || echo no)"
    printf '%-14s %-44s %s\n' ExplainHyper "$EH_REV"   "$([ -d "$HERE/ExplainHyper/explainhyper" ] && echo yes || echo no)"
}

main() {
    local want=("$@")
    if [ ${#want[@]} -eq 0 ]; then want=(corp explainhyper cats); fi
    for w in "${want[@]}"; do
        case "$w" in
            --list|-l)     list; return 0 ;;
            corp|CORP)     fetch_corp ;;
            explainhyper|ExplainHyper|eh) fetch_explainhyper ;;
            cats|CATS)     fetch_cats ;;
            -h|--help)     sed -n '2,20p' "$0"; return 0 ;;
            *)             die "unknown target: $w  (corp | explainhyper | cats)" ;;
        esac
    done
    echo
    list
}

main "$@"
