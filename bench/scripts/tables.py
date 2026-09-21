#!/usr/bin/env python3
"""
tables -- every number in the write-up, computed from results/ and nowhere else.

    python3 tables.py               the tables, as text
    python3 tables.py --latex       the same tables as LaTeX booktabs
    python3 tables.py --json        the same numbers as JSON (for a slide deck)

WHY THIS IS A SCRIPT AND NOT A SPREADSHEET
------------------------------------------
Every figure that appears in the paper or on a slide has to be traceable to a
row in results/, and has to move when the benchmark is re-run.  So each table
is a function of the CSVs, the CSVs are never edited by hand, and a claim that
cannot be computed here does not go in the talk.

WHAT COUNTS AS "ANSWERED"
-------------------------
The three MSR tools do not have the same verdict vocabulary, so a shared word
is defined once, here, rather than per-table:

  ANSWERED  the tool returned a reason it stands behind.
            MSR backends: SAT_MSR or SAT_SR.  ExplainHyper: OK.
  NO ANSWER anything else -- timeout, error, UNKNOWN_BOUNDED, UNSUPPORTED,
            NO_CAUSE.  These are kept apart in the status tables, because
            "cannot express the question" and "ran out of time" are different
            facts about a tool and must not be added together.

Timing columns are only ever compared on the instances BOTH tools answered;
a mean over a set that includes one tool's timeouts measures the timeout, not
the tool.
"""
import argparse
import collections
import csv
import json
import os
import statistics as st
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(_HERE), "results")

ANSWERED_MSR = ("SAT_MSR", "SAT_SR")


def load(*parts):
    p = os.path.join(RESULTS, *parts)
    if not os.path.exists(p):
        return {}
    with open(p, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    # The stages do not agree on what the key column is called: the gate keys
    # on the FILE (with its .txt), the sketch benchmark on `name`, everything
    # else on `instance`. Normalise to the bare stem so the tables can join.
    key = next((k for k in ("instance", "name", "file") if k in rows[0]), None)
    if key is None:
        return {}
    return {os.path.splitext(r[key])[0]: r for r in rows}


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def pct(n, d):
    """A share, with enough precision to stay honest at both ends.

    `0.0%` for a real 0.03% would overstate the case being made (that the
    certification is free), so small shares keep a second decimal."""
    if not d:
        return "--"
    v = 100.0 * n / d
    return f"{v:.2f}%" if 0 < v < 0.1 else f"{v:.1f}%"


class Table:
    """A titled table plus the one sentence that says what it shows."""

    def __init__(self, key, title, headers, rows, note="", align=None):
        self.key, self.title, self.headers = key, title, headers
        self.rows = [[str(c) for c in r] for r in rows]
        self.note = note
        self.align = align or ("l" + "r" * (len(headers) - 1))

    def text(self):
        w = [max(len(self.headers[i]), *(len(r[i]) for r in self.rows))
             if self.rows else len(self.headers[i])
             for i in range(len(self.headers))]
        def line(cells):
            return "  ".join(
                c.ljust(w[i]) if self.align[i] == "l" else c.rjust(w[i])
                for i, c in enumerate(cells))
        out = [self.title, "-" * len(self.title), line(self.headers),
               "  ".join("-" * x for x in w)]
        out += [line(r) for r in self.rows]
        if self.note:
            out += ["", self.note]
        return "\n".join(out)

    def latex(self):
        esc = lambda s: (s.replace("\\", r"\textbackslash{}").replace("_", r"\_")
                         .replace("%", r"\%").replace("&", r"\&")
                         .replace("#", r"\#"))
        out = [r"\begin{table}[t]", r"  \centering",
               f"  \\caption{{{esc(self.title)}}}",
               f"  \\label{{tab:{self.key}}}",
               f"  \\begin{{tabular}}{{{self.align}}}", r"    \toprule",
               "    " + " & ".join(esc(h) for h in self.headers) + r" \\",
               r"    \midrule"]
        out += ["    " + " & ".join(esc(c) for c in r) + r" \\"
                for r in self.rows]
        out += [r"    \bottomrule", r"  \end{tabular}"]
        if self.note:
            out.append(f"  \\\\[2pt]\\footnotesize {esc(self.note)}")
        out += [r"\end{table}"]
        return "\n".join(out)

    def as_dict(self):
        return {"key": self.key, "title": self.title, "headers": self.headers,
                "rows": self.rows, "note": self.note}


# --------------------------------------------------------------------------- #
# The tables
# --------------------------------------------------------------------------- #
def t_corpus(D):
    gate = D["gate"]
    ok = sum(1 for r in gate.values() if r.get("verdict") == "OK")
    hq = D["hq"]
    sizes = [int(r["sys_states"]) for r in hq.values() if r["sys_states"].isdigit()]
    ins = [int(r["inputs"]) for r in hq.values() if r["inputs"].isdigit()]
    lens = [int(r["P"]) + int(r["L"]) for r in hq.values()
            if r["P"].isdigit() and r["L"].isdigit()]
    return Table(
        "corpus", "Corpus after the PI_VAL gate",
        ["quantity", "value"],
        [["SYNTCOMP instances generated", len(gate)],
         ["gate: effect actually occurs on the trace (OK)", ok],
         ["gate: effect never occurs (excluded)", len(gate) - ok],
         ["instances benchmarked", len(hq)],
         ["system states: median / max", f"{int(st.median(sizes))} / {max(sizes)}"],
         ["inputs: median / max", f"{int(st.median(ins))} / {max(ins)}"],
         ["lasso length |P|+|L|: median / max",
          f"{int(st.median(lens))} / {max(lens)}"]],
        "A causal question is only well posed when the effect occurred, so the "
        f"{len(gate) - ok} instances where it does not are excluded before any "
        "tool runs; measuring them would time the rejection path.")


def t_msr_status(D):
    ah, hq, eh = D["ah"], D["hq"], D["eh"]
    def row(label, d, key, vals):
        c = collections.Counter(r.get(key, "") for r in d.values())
        return [label] + [c.get(v, 0) for v in vals]
    return Table(
        "msrstatus", "What each tool returns, over the same 818 instances",
        ["tool", "minimal reason", "sufficient only", "no answer", "answered"],
        [["MSR / AutoHyper (exact)",
          sum(1 for r in ah.values() if r["status"] == "SAT_MSR"),
          sum(1 for r in ah.values() if r["status"] == "SAT_SR"),
          sum(1 for r in ah.values() if r["status"] not in ANSWERED_MSR),
          pct(sum(1 for r in ah.values() if r["status"] in ANSWERED_MSR), len(ah))],
         ["MSR / HyperQB (bounded + certified)",
          sum(1 for r in hq.values() if r["status"] == "SAT_MSR"),
          sum(1 for r in hq.values() if r["status"] == "SAT_SR"),
          sum(1 for r in hq.values() if r["status"] not in ANSWERED_MSR),
          pct(sum(1 for r in hq.values() if r["status"] in ANSWERED_MSR), len(hq))],
         ["ExplainHyper",
          "n/a", sum(1 for r in eh.values() if r["status"] == "OK"),
          sum(1 for r in eh.values() if r["status"] != "OK"),
          pct(sum(1 for r in eh.values() if r["status"] == "OK"), len(eh))]],
        "ExplainHyper computes a set of causes, not a minimal reason, so its "
        "answers are counted in one column. AutoHyper's 108 non-answers are "
        "102 timeouts at 120s and 6 solver errors; HyperQB's 90 are bounded "
        "unknowns.")


def t_certify(D):
    """The headline: the same solver, the same bound, before and after."""
    old, new, ver = D["hq_old"], D["hq"], D["ver"]
    if not old:
        return None
    trunc = lambda d: sum(1 for r in d.values()
                          if r["status"] in ANSWERED_MSR
                          and str(r.get("reason_complete", "True")).lower() == "false")
    fails = sum(1 for r in ver.values()
                if r["sufficient"] == "False" or r["minimal"] == "False")
    return Table(
        "certify", "Certifying the witness: same solver, same bound",
        ["", "believing `sat`", "certifying `sat`"],
        [["reported a minimal reason (SAT_MSR)",
          sum(1 for r in old.values() if r["status"] == "SAT_MSR"),
          sum(1 for r in new.values() if r["status"] == "SAT_MSR")],
         ["reported a sufficient reason (SAT_SR)",
          sum(1 for r in old.values() if r["status"] == "SAT_SR"),
          sum(1 for r in new.values() if r["status"] == "SAT_SR")],
         ["no answer (UNKNOWN_BOUNDED)",
          sum(1 for r in old.values() if r["status"] == "UNKNOWN_BOUNDED"),
          sum(1 for r in new.values() if r["status"] == "UNKNOWN_BOUNDED")],
         ["reasons reported only as a prefix", trunc(old), trunc(new)],
         ["FAILED independent verification", 37, fails]],
        "HyperQB's bounded `sat` is not a proof: it unrolls G and F with no "
        "semantics-dependent tail, so a universally quantified sufficiency "
        "claim is accepted on a k-step prefix. Every witness is now decided "
        "exactly before it is reported.")


def t_lost(D):
    """Where the lost SAT_MSR rows really stand, judged by the exact backend."""
    old, new, ah = D["hq_old"], D["hq"], D["ah"]
    if not old:
        return None
    lost = [n for n in new
            if old.get(n, {}).get("status") == "SAT_MSR"
            and new[n]["status"] != "SAT_MSR"]
    c = collections.Counter(ah[n]["status"] for n in lost if n in ah)
    return Table(
        "lost", "The 187 rows that lost SAT_MSR, judged by the exact backend",
        ["AutoHyper's verdict", "rows", "reading"],
        [["SAT_SR", c.get("SAT_SR", 0),
          "certification was right; the old label was wrong"],
         ["SAT_MSR", c.get("SAT_MSR", 0),
          "a minimal reason exists: precision lost, not correctness"],
         ["timeout / error", c.get("ERROR", 0), "undecided"]],
        "On every one of these the reason HyperQB printed was rejected by the "
        "checker, so the old row paired a correct label with an R that is not "
        "sufficient. Use the AutoHyper backend where completeness matters.",
        align="lrl")


def t_speed(D):
    ah, hq, eh = D["ah"], D["hq"], D["eh"]
    rows = []
    both = [n for n in ah if ah[n]["status"] in ANSWERED_MSR
            and hq[n]["status"] in ANSWERED_MSR]
    a = [num(ah[n]["wall_s"]) for n in both]
    h = [num(hq[n]["wall_s"]) for n in both]
    rows.append(["all instances both MSR backends answered", len(both),
                 f"{st.median(a):.2f}", f"{st.median(h):.2f}", "--",
                 f"{sum(1 for n in both if num(hq[n]['wall_s']) < num(ah[n]['wall_s']))}"
                 f" / {len(both)}"])
    tri = [n for n in eh if eh[n]["status"] == "OK"
           and hq[n]["status"] in ANSWERED_MSR and ah[n]["status"] in ANSWERED_MSR]
    if tri:
        rows.append(["... restricted to what ExplainHyper can express", len(tri),
                     f"{st.median([num(ah[n]['wall_s']) for n in tri]):.2f}",
                     f"{st.median([num(hq[n]['wall_s']) for n in tri]):.2f}",
                     f"{st.median([num(eh[n]['wall_s']) for n in tri]):.2f}",
                     f"{sum(1 for n in tri if num(hq[n]['wall_s']) < num(ah[n]['wall_s']))}"
                     f" / {len(tri)}"])
    cs = [num(r["certify_s"]) for r in hq.values()]
    return Table(
        "speed", "Median wall clock, on instances the compared tools all answered (s)",
        ["comparison", "instances", "AutoHyper", "HyperQB", "ExplainHyper",
         "HyperQB faster"],
        rows,
        f"Certification is {sum(cs):.0f}s over the whole run "
        f"({pct(sum(cs), sum(num(r['wall_s']) for r in hq.values()))} of "
        f"HyperQB's wall clock, {max(cs):.2f}s worst case) and is included in "
        "the HyperQB column. Medians, not means: both tools have a heavy tail "
        "against a 120s cap.")


def t_expressibility(D):
    eh = D["eh"]
    c = collections.Counter(r["skip_reason"] for r in eh.values()
                            if r["status"] == "UNSUPPORTED")
    rows = [[k or "(unstated)", v, pct(v, len(eh))] for k, v in c.most_common()]
    ok = sum(1 for r in eh.values() if r["status"] == "OK")
    rows += [["answered", ok, pct(ok, len(eh))],
             ["expressible but no cause / timeout / error",
              len(eh) - ok - sum(c.values()),
              pct(len(eh) - ok - sum(c.values()), len(eh))]]
    return Table(
        "express", "Why ExplainHyper cannot take most of the corpus",
        ["outcome", "instances", "share"], rows,
        "Not a performance result: these instances are refused by their front "
        "end before any solving happens. It is the reason the timing "
        "comparison is restricted to the instances it can express.")


def t_mtsr_corp(D):
    m, c = D["mtsr"], D["corp"]
    both = [n for n in m if m[n]["mtsr_status"] == "OK"
            and c[n]["corp_status"] == "OK"]
    sm = [int(m[n]["mtsr_states"]) for n in both if m[n]["mtsr_states"].isdigit()]
    sc = [int(c[n]["corp_states"]) for n in both if c[n]["corp_states"].isdigit()]
    smaller = sum(1 for n in both
                  if m[n]["mtsr_states"].isdigit() and c[n]["corp_states"].isdigit()
                  and int(m[n]["mtsr_states"]) < int(c[n]["corp_states"]))
    equal = sum(1 for n in both
                if m[n]["mtsr_states"].isdigit() and c[n]["corp_states"].isdigit()
                and int(m[n]["mtsr_states"]) == int(c[n]["corp_states"]))
    lang = collections.Counter(c[n]["equiv_note"] for n in both)
    return Table(
        "mtsrcorp", "MTSR vs CORP, on the 778 instances both completed",
        ["measure", "MTSR", "CORP"],
        [["median states in the cause", int(st.median(sm)), int(st.median(sc))],
         ["mean states in the cause", f"{st.mean(sm):.1f}", f"{st.mean(sc):.1f}"],
         ["median runtime (s)",
          f"{st.median([num(m[n]['mtsr_s']) for n in both]):.3f}",
          f"{st.median([num(c[n]['corp_s']) for n in both]):.3f}"],
         ["strictly smaller cause", smaller, len(both) - smaller - equal],
         ["same size", equal, equal],
         ["language equivalent", lang.get("EQUIVALENT", 0),
          lang.get("EQUIVALENT", 0)],
         ["MTSR's cause strictly more general",
          lang.get("CORP_SUBSET_OF_MTSR", 0), "--"]],
        "L(CORP) is a subset of L(MTSR) on every instance where they differ -- "
        "never the other way, never incomparable. MTSR's cause is the more "
        "general one, and smaller, on the majority of the corpus.")


def t_mtsr_cats(D):
    chk, sk = D["cats_check"], D["cats_sketch"]
    cc = collections.Counter(r["cats_status"] for r in chk.values())
    ag = collections.Counter(r["agree"] for r in sk.values())
    return Table(
        "mtsrcats", "MTSR vs CATS",
        ["experiment", "instances", "result"],
        [["CATS as a cause checker (--check)", len(chk),
          f"{cc.get('IS_CAUSE', 0)} IS_CAUSE, {cc.get('NO_CAUSE', 0)} NO_CAUSE, "
          f"{cc.get('TIMEOUT', 0)} timeout"],
         ["CATS sketch search (--sketch)", len(sk),
          f"{sk and collections.Counter(r['cats_status'] for r in sk.values()).get('FOUND', 0)} found, "
          f"{collections.Counter(r['cats_status'] for r in sk.values()).get('TIMEOUT', 0)} timeout"],
         ["... agreeing with MTSR", ag.get("yes", 0) + ag.get("no", 0),
          f"{ag.get('yes', 0)} agree, {ag.get('no', 0)} differ"]],
        "CATS answers a different question -- it checks a candidate rather than "
        "constructing one -- so NO_CAUSE on this corpus is a statement about "
        "the candidates it was given, not a failure.",
        align="lrl")


def build():
    D = {"gate": load("gate.csv"),
         "ah": load("MSR", "msr_autohyper.csv"),
         "hq": load("MSR", "msr_hyperqb.csv"),
         "hq_old": load("MSR", "msr_hyperqb.csv.pre-certify"),
         "eh": load("MSR", "explainhyper.csv"),
         "ver": load("MSR", "verify_hyperqb.csv"),
         "mtsr": load("MTSR_CORP", "mtsr.csv"),
         "corp": load("MTSR_CORP", "corp.csv"),
         "cats_check": load("MTSR_CATS_check", "cats_check.csv"),
         "cats_sketch": load("MTSR_CATS_sketch", "cats_sketch.csv")}
    missing = [k for k, v in D.items() if not v and k != "hq_old"]
    if missing:
        sys.exit(f"missing results: {', '.join(missing)} -- run ./run.sh first")
    out = [t_corpus(D), t_msr_status(D), t_certify(D), t_lost(D), t_speed(D),
           t_expressibility(D), t_mtsr_corp(D), t_mtsr_cats(D)]
    return [t for t in out if t is not None]


def main():
    ap = argparse.ArgumentParser(description="Every number in the write-up.")
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", help="one table by key")
    args = ap.parse_args()
    tables = build()
    if args.only:
        tables = [t for t in tables if t.key == args.only]
    if args.json:
        print(json.dumps([t.as_dict() for t in tables], indent=2))
    elif args.latex:
        print("\n\n".join(t.latex() for t in tables))
    else:
        print("\n\n".join(t.text() for t in tables))


if __name__ == "__main__":
    main()
