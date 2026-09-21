# tools — the baselines, fetched not vendored

```sh
./fetch.sh            # all three
./fetch.sh --list     # the pins, and what is already here
./fetch.sh corp       # one of them
```

Nothing here is ours. Each baseline is fetched at the **exact commit the
numbers in `../results/` were measured with**:

| tool | upstream | commit | license | build |
|---|---|---|---|---|
| CATS | [`cats-tool/cats`](https://github.com/cats-tool/cats) | `6096d741449e084a792af00c28bd9bbea0cb55ea` | GPL-3.0 | .NET 7 SDK, ~30 s |
| CORP | [`reactive-systems/corp`](https://github.com/reactive-systems/corp) | `762c171af4e97b3bb6fa6a0af0a26f4b498ed372` | GPL-3.0 | none (Python + spot) |
| ExplainHyper | [`reactive-systems/explaining-hyperproperty-violations`](https://github.com/reactive-systems/explaining-hyperproperty-violations) | `9f894a098df2677b6a78e29be4ec4acb7f57038d` | MIT | none (its own env) |

A commit hash says what was measured more precisely than a copied directory
does, and it keeps someone else's tool out of our history. The cost is that
`fetch.sh` needs the network once; in the benchmark image it is run at build
time (see `../../Dockerfile.bench`).

## The one tool we change: ExplainHyper

Three diffs in `patches/`, applied by `fetch.sh` in this order:

| patch | why |
|---|---|
| `altautomaton-get_label.patch` | `altautomaton.py:111` compares a formula against a **bound method** (`t2.get_label`, no parentheses). Any top-level `F` crashes their tool. One character. |
| `parser-traceProperty.patch`, `explain_hyper-traceProperty.patch` | add an opt-in `-traceProperty` flag. Their tool is hardwired to two traces; a lifted **trace** property mentions only trace 0, so leaving the padding trace in the candidate set makes the minimality search enumerate twice as many subsets as their own Def. 6 requires. Off by default — without the flag their behaviour is unchanged. |

The first is a bug fix without which the comparison cannot run at all; the
second is a flag, not a change of semantics, and it is the reason their times
are their algorithm's rather than an artefact of the encoding we hand them.

Sanity check after patching — this must still reproduce Table 1, row 1
(`#C = 2`) of their paper:

```sh
cd ExplainHyper/examples
"$EXPLAINHYPER_ENV_PY" ../explainhyper/explain_hyper.py \
    -aag running_example.aag -cex running_example.cex \
    -stateMachine running_example.dot -outName /tmp/re \
    -f "Forall (Forall (Implies (G (Eq (AP \"hi\" 0) (AP \"hi\" 1))) (G (Eq (AP \"lo\" 0) (AP \"lo\" 1)))))"
```

CATS and CORP are used exactly as published.

## What CATS's `paths.json` points at

`fetch.sh` writes `CATS/app/paths.json` pointing at
`../scripts/solver_wrappers/`, **not** at spot. CATS builds on FsOmegaLib,
which starts spot and then calls `WaitForExit()` with no timeout and no memory
cap (`src/FsOmegaLib/Util.fs:62`); one unguarded `autfilt` child reached 8.4 GB
and drove a 9 GB machine to a load of 42. The wrappers cap the leaf process,
which is the only thing that can be capped — the .NET process itself fails at
startup under any `ulimit -v`. See `../scripts/solver_wrappers/autfilt`.

## Environments

`fetch.sh` builds; it does not install interpreters. CORP runs on the spot
environment (`$SPOT_ENV_PY`), ExplainHyper on its own
(`$EXPLAINHYPER_ENV_PY`, `py-aiger` / `python-sat` / `satispy`, which do not
coexist with the spot env), CATS on the .NET 7 runtime. `../README.md`
(Environments) has the table; `../../Dockerfile.bench` builds all of them.
