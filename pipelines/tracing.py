"""DeepEval tracing seam for the QA flow pipeline.

Every other module imports tracing helpers from HERE, never from `deepeval` directly. That
buys three things:

1. **No hard dependency.** deepeval is heavy and is not installed on the system interpreter
   (only in .venv). If it is missing — or if PIPELINE_TRACING=0 — every helper degrades to a
   no-op and `pipelines.flow` still imports and runs. This mirrors the convention already in
   common.py, which imports claude_agent_sdk *inside* run_agent specifically so the module
   stays importable before `pip install` has been run.
2. **A local sink.** Verified against deepeval 4.2.0: with no Confident AI API key the library
   builds the trace in memory, logs "No Confident AI API key found. Skipping trace posting."
   and writes NOTHING to disk. Tracing locally is therefore worthless without the sink below.
3. **One place to control the destination.** Cloud export is explicitly opt-in and never
   implied by an env var merely being present — these transcripts carry ticket content, PR
   diffs and live-app narration from a bypassPermissions run.

Symbols are imported INDIVIDUALLY rather than in one `from deepeval.tracing import a, b, c`.
A single renamed symbol in a future deepeval release would make an atomic import raise, which
would silently downgrade the whole pipeline to no-op tracing and look like "tracing is broken"
rather than "one symbol moved".
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "observe", "update_current_span", "update_current_trace", "update_llm_span",
    "flush_traces", "set_trace_output", "tracing_banner", "TRACING_ENABLED",
]

_DISABLED = os.environ.get("PIPELINE_TRACING", "1") == "0"
# Cloud export must be asked for by name. Merely having CONFIDENT_API_KEY set in the
# environment (or inherited by some future CI agent) must NOT start shipping transcripts.
_CLOUD_OPT_IN = os.environ.get("PIPELINE_TRACING_CLOUD", "0") == "1"

# Where the local sink writes. Set by flow.main() once the story dir is known.
_output_path: list = [None]


# --------------------------------------------------------------------------- no-op fallbacks
def _noop_observe(_func=None, **_kwargs):
    """Identity decorator.

    Returning the function UNCHANGED (rather than wrapping it) is deliberate: it preserves
    `asyncio.iscoroutinefunction`, which matters because run_agent is a coroutine consumed by
    `asyncio.run`. A sync wrapper around an async function would break the caller's `await`.
    """
    if _func is not None and callable(_func):
        return _func
    return lambda fn: fn


def _noop(*_args, **_kwargs) -> None:
    return None


# ------------------------------------------------------------------------------ real imports
observe = _noop_observe
update_current_span = _noop
update_current_trace = _noop
update_llm_span = _noop
flush_traces = _noop
TRACING_ENABLED = False
_import_error: Optional[str] = None
_missing: list = []

if not _DISABLED:
    try:
        import deepeval.tracing as _dt  # type: ignore

        def _grab(name, fallback):
            fn = getattr(_dt, name, None)
            if fn is None:
                _missing.append(name)
                return fallback
            return fn

        observe = _grab("observe", _noop_observe)
        update_current_span = _grab("update_current_span", _noop)
        update_current_trace = _grab("update_current_trace", _noop)
        update_llm_span = _grab("update_llm_span", _noop)
        flush_traces = _grab("flush_traces", _noop)
        TRACING_ENABLED = observe is not _noop_observe
    except Exception as exc:  # ImportError, or deepeval blowing up on import
        _import_error = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------------- the sink
def _dump_span(span: Any) -> dict:
    """Dump ONE span against its own subclass schema, then recurse.

    This is not a stylistic choice. `Trace.root_spans` is typed as the BASE class, so
    `trace.model_dump()` serializes every child through the BaseSpan schema and SILENTLY DROPS
    subclass fields — `model`, `input_token_count` and `output_token_count` on LlmSpan,
    `available_tools`/`agent_handoffs` on AgentSpan. Verified against deepeval 4.2.0: a live
    LlmSpan carries model="claude-opus-5", and the same span inside trace.model_dump() has no
    `model` key at all. Since the model and token counts are the whole point of an llm span,
    dumping the parent alone would throw away the payload this pipeline exists to record.
    """
    try:
        data = span.model_dump(mode="json")
    except Exception as exc:
        data = {"name": getattr(span, "name", None),
                "_dump_error": f"{type(exc).__name__}: {exc}"}
    # span.model_dump() serialized ITS children through BaseSpan too, so replace them wholesale.
    data["children"] = [_dump_span(c) for c in (getattr(span, "children", None) or [])]
    return data


def _serialize(trace: Any) -> dict:
    """Trace and BaseSpan are pydantic v2 models (verified), so model_dump does the work.

    `default=str` on the json round-trip catches anything model_dump leaves as a live object
    (datetimes, an Exception in `error`, a WindowsPath someone put in metadata).
    """
    try:
        data = trace.model_dump(mode="json")
        data["root_spans"] = [_dump_span(s) for s in (getattr(trace, "root_spans", None) or [])]
        return json.loads(json.dumps(data, default=str))
    except Exception as exc:
        return {"_serialization_error": f"{type(exc).__name__}: {exc}",
                "_repr": repr(trace)[:2000]}


def _write_trace(trace: Any) -> None:
    path = _output_path[0]
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_serialize(trace)) + "\n")
    except Exception as exc:
        print(f"[tracing] local sink failed: {type(exc).__name__}: {exc}")


def _install_sink() -> None:
    """Wrap trace_manager.post_trace — the exact function that short-circuits with no API key.

    `_original` is captured as a BOUND method, so calling it with just `trace` is correct;
    assigning to the instance attribute shadows the class method without needing `self`.
    """
    tm = getattr(_dt, "trace_manager", None)
    original = getattr(tm, "post_trace", None)
    if tm is None or original is None:
        return

    def sink(trace, *args, **kwargs):
        _write_trace(trace)
        if not _CLOUD_OPT_IN:
            return None          # never post when cloud export was not explicitly requested
        try:
            return original(trace, *args, **kwargs)
        except Exception as exc:
            print(f"[tracing] cloud post failed: {type(exc).__name__}: {exc}")
            return None

    tm.post_trace = sink


if TRACING_ENABLED:
    _install_sink()


def set_trace_output(path) -> None:
    """Point the local sink at a file. Call before the traced root runs."""
    _output_path[0] = Path(path) if path else None


def tracing_banner() -> str:
    """One line naming the destination. Printed at startup so tracing is never silently off."""
    if _DISABLED:
        return "[tracing] disabled (PIPELINE_TRACING=0)"
    if _import_error:
        return f"[tracing] disabled - deepeval unavailable ({_import_error})"
    if not TRACING_ENABLED:
        return "[tracing] disabled - deepeval.tracing.observe not found"
    dest = "Confident AI (cloud) + local file" if _CLOUD_OPT_IN else "local file only"
    warn = f" [missing symbols: {', '.join(_missing)}]" if _missing else ""
    return f"[tracing] deepeval active -> {dest}{warn}"
