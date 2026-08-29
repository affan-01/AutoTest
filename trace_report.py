"""Render a pipeline trace.jsonl as a readable report.

Plain stdlib and plain JSON on purpose: no deepeval import, so this runs on the system
interpreter without the venv. `deepeval inspect` deliberately does NOT read these files - that
command consumes test_run_*.json produced by evaluate()/pytest, whereas these traces come from
@observe and are written by pipelines/tracing.py's own sink (deepeval drops them entirely when
no Confident AI key is set).

Usage:
    python -m pipelines.trace_report <story-id>          # artifacts/flow/<id>/trace.jsonl
    python -m pipelines.trace_report path/to/trace.jsonl
    python -m pipelines.trace_report <story-id> --all    # every run, not just the latest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fmt_ms(ms):
    if not ms:
        return "-"
    s = ms / 1000.0
    return f"{s:.0f}s" if s < 120 else f"{s/60:.1f}m"


def _walk(span, depth, rows):
    rows.append((depth, span))
    for child in span.get("children") or []:
        _walk(child, depth + 1, rows)


def render(trace: dict) -> None:
    md = trace.get("metadata") or {}
    print("=" * 78)
    print(f"TRACE  {trace.get('name') or '(unnamed)'}   status={trace.get('status')}")
    print(f"  story        : {md.get('story_id') or trace.get('thread_id')}")
    print(f"  tags         : {', '.join(trace.get('tags') or []) or '-'}")
    if md.get("stages_run") is not None:
        print(f"  stages run   : {', '.join(md.get('stages_run') or []) or '-'}")
        print(f"  stages skipped: {', '.join(md.get('stages_skipped') or []) or '-'}")
    if md.get("outcome"):
        print(f"  outcome      : {md['outcome']}")

    rows = []
    for root in trace.get("root_spans") or []:
        _walk(root, 0, rows)

    print("\n  SPANS")
    total_cost = 0.0
    for depth, s in rows:
        m = s.get("metadata") or {}
        pad = "    " + "   " * depth
        bits = []
        if "ok" in m:
            bits.append(f"ok={m['ok']}")
        if m.get("num_turns") is not None:
            flag = " HIT-MAX" if m.get("hit_max_turns") else ""
            bits.append(f"turns={m['num_turns']}/{m.get('max_turns_configured')}{flag}")
        # Prefer cost_usd (summed from model_usage). total_cost_usd omits sub-agent spend
        # entirely and under-reported a real run by 9x, so it is never the headline figure.
        cost = m.get("cost_usd", m.get("total_cost_usd"))
        if cost is not None:
            total_cost += cost or 0
            bits.append(f"${cost:.4f}")
        if m.get("duration_ms"):
            bits.append(f"{_fmt_ms(m['duration_ms'])}")
        status = s.get("status", "")
        print(f"{pad}- {s.get('name')} [{status}] {'  '.join(bits)}")
        if s.get("model"):
            print(f"{pad}    model: {s['model']}"
                  f"  in={s.get('input_token_count')}  out={s.get('output_token_count')}")
        wall, dur, api = m.get("wall_ms"), m.get("duration_ms"), m.get("duration_api_ms")
        if m.get("delegated"):
            # ResultMessage described only the parent's turns, so its split is meaningless.
            print(f"{pad}    time : {_fmt_ms(wall)} wall  "
                  f"(SDK reported only {_fmt_ms(dur)} - sub-agent time not counted)")
            print(f"{pad}    NOTE : DELEGATED to a sub-agent - "
                  f"num_turns={m.get('num_turns')} counts only the parent's turns")
        elif dur and api:
            pct = (api / dur * 100) if dur else 0
            print(f"{pad}    time : {_fmt_ms(dur)} total, {_fmt_ms(api)} model "
                  f"({pct:.0f}% model / {100 - pct:.0f}% tools)")
        cmp_ = m.get("cost_reported_vs_usage") or {}
        if cmp_.get("diverges"):
            print(f"{pad}    COST : ${cmp_.get('model_usage_sum'):.4f} actual "
                  f"(SDK total_cost_usd said ${cmp_.get('total_cost_usd'):.4f})")
        if m.get("stop_reason"):
            print(f"{pad}    stop : {m['stop_reason']}"
                  f"   is_error={m.get('is_error')}   api_error={m.get('api_error_status')}")
        for key, label in (("permission_denials", "DENIED"), ("errors", "ERRORS")):
            if m.get(key):
                print(f"{pad}    {label}: {m[key]}")
        if m.get("tool_calls"):
            print(f"{pad}    tools: {', '.join(m['tool_calls'])}")
        if m.get("artifact_count") is not None:
            print(f"{pad}    artifacts: {m['artifact_count']}")
            for a in (m.get("new_artifacts") or [])[:6]:
                print(f"{pad}      - {a}")
        if m.get("note"):
            print(f"{pad}    note : {m['note']}")
        for key in ("eval_groundedness", "eval_summarization"):
            e = m.get(key)
            if e:
                score = f"{e['score']:.2f}" if e.get("score") is not None else "n/a"
                verdict = "PASS" if e.get("success") else "BELOW THRESHOLD / FAILED"
                print(f"{pad}    eval [{key}]: score={score} threshold={e.get('threshold')} {verdict}")
                print(f"{pad}      reason: {str(e.get('reason'))[:300]}")
        if s.get("error"):
            print(f"{pad}    ERROR: {str(s['error'])[:160]}")
        if m.get("model_usage"):
            for model, u in m["model_usage"].items():
                print(f"{pad}    usage: {model}: in={u.get('inputTokens')} "
                      f"out={u.get('outputTokens')} cacheRead={u.get('cacheReadInputTokens')} "
                      f"${u.get('costUSD')}")
    print(f"\n  TOTAL COST: ${total_cost:.4f}   (summed from model_usage)")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_all = "--all" in sys.argv
    if not args:
        print(__doc__)
        return 2
    target = Path(args[0])
    if not target.exists():
        target = REPO_ROOT / "artifacts" / "flow" / args[0] / "trace.jsonl"
    if not target.exists():
        print(f"No trace found at {target}")
        return 1
    lines = [l for l in target.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"{target}  ({len(lines)} run(s))\n")
    for line in (lines if show_all else lines[-1:]):
        render(json.loads(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
