"""DeepEval quality-metric seam for the QA flow pipeline.

Wires the two metrics picked as the starting set for this pipeline (chat/PR discussion,
2026-08-28): a **Groundedness** G-Eval on `stage_generate`'s output — catches test cases that
assert screens, fields, or business rules with no basis in the story — and a **Summarization**
metric on `stage_report`'s qa-report.md — catches a ship/no-ship writeup that misrepresents what
`execution_log.json` actually said. Those two guard the pipeline's costliest failure modes
(fabricated test cases, misleading ship calls); Tool Correctness and Task Completion on the
other stages are a natural follow-up once these are proven out, not built speculatively here.

Same graceful-degradation contract as tracing.py: if deepeval or the metrics submodule is
unavailable, every helper here becomes a no-op and flow.py keeps running with evals simply
absent from the trace/report — an eval outage must never fail the pipeline.

The judge model is `_ClaudeAgentSdkModel` below, NOT deepeval's built-in `AnthropicModel`.
AnthropicModel requires a standalone Anthropic Console API key, which not every org grants
self-serve (and getting one issued can be its own approval process). Every other stage in this
pipeline already authenticates through the Claude Agent SDK's own (Claude Code / claude.ai)
login with no separate key at all — see pipelines/common.py::run_agent — so the judge reuses
that exact mechanism instead of depending on a credential you may not have.

Evals are opt-in via PIPELINE_EVALS=1, separately from PIPELINE_TRACING=1. Unlike tracing (which
is free once deepeval is installed), every call here is a real judge-model request that consumes
real usage on whatever Claude Code login is active — the same "must be asked for by name" rule
tracing.py applies to Confident AI cloud export applies here to spending usage on every run.

Deliberately NOT a pipeline gate yet: outcomes are attached to the trace span metadata and
printed to stdout (stage_generate/stage_report wire them into qa-report.md indirectly via the
trace), but a low score does not fail the stage or flip ship/no-ship. Thresholds are uncalibrated
against zero real runs so far — gating on them now would be exactly the kind of half-finished
feature this repo's own conventions warn against. Revisit once a handful of real runs show the
scores are trustworthy.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from pipelines.common import REPO_ROOT

__all__ = ["EvalOutcome", "evaluate_generate_groundedness", "evaluate_report_summary", "evals_banner"]

_ENABLED = os.environ.get("PIPELINE_EVALS", "0") == "1"
# claude-sonnet-5 by default: matches the model driving this session, and is materially cheaper
# than a stronger judge for a call that runs on every pipeline invocation. Override via
# PIPELINE_EVAL_MODEL if a stronger judge is worth the cost.
_MODEL_NAME = os.environ.get("PIPELINE_EVAL_MODEL", "claude-sonnet-5")

# Truncation caps: these feed an LLM judge prompt, not a human reader — keep them well under the
# judge model's context window without needing to count tokens precisely.
_MAX_CONTEXT_CHARS = 8000
_MAX_OUTPUT_CHARS = 12000

_import_error: Optional[str] = None
_GEval = None
_SummarizationMetric = None
_SingleTurnParams = None
_LLMTestCase = None
_ClaudeAgentSdkModel = None

if _ENABLED:
    try:
        from deepeval.metrics import GEval as _GEval  # type: ignore
        from deepeval.metrics import SummarizationMetric as _SummarizationMetric  # type: ignore
        from deepeval.test_case import LLMTestCase as _LLMTestCase  # type: ignore
        from deepeval.test_case import SingleTurnParams as _SingleTurnParams  # type: ignore
        from deepeval.models import DeepEvalBaseLLM as _DeepEvalBaseLLM  # type: ignore

        class _ClaudeAgentSdkModel(_DeepEvalBaseLLM):
            """A deepeval judge model backed by THIS repo's own Claude Agent SDK auth path,
            instead of a standalone Anthropic API key (see module docstring for why).

            One live wrinkle verified against this exact environment (2026-08-28): if
            ANTHROPIC_API_KEY happens to be set — even to an invalid value, as it was here — the
            Claude CLI's own precedence rules make it shadow the working claude.ai login, and the
            call hangs/fails ("claude.ai connectors are disabled because ANTHROPIC_API_KEY ...
            takes precedence"). `env={"ANTHROPIC_API_KEY": ""}` below neutralizes that for JUST
            this subprocess — verified that subprocess_cli.py merges options.env over the
            inherited environment per-key, so this does not touch the calling process's own
            os.environ or anything else. Deliberately scoped to this judge call, not applied in
            pipelines/common.py::run_agent — changing auth precedence for the four real pipeline
            stages is a separate decision this module does not make unilaterally.
            """

            def __init__(self, model_name: str):
                self._model_name = model_name
                super().__init__(model_name)

            def load_model(self):
                return self

            def get_model_name(self) -> str:
                return self._model_name

            async def a_generate(self, prompt: str, schema=None) -> str:
                from claude_agent_sdk import (  # type: ignore
                    query, ClaudeAgentOptions, AssistantMessage, ResultMessage,
                )

                options = ClaudeAgentOptions(
                    cwd=str(REPO_ROOT),
                    allowed_tools=[],
                    max_turns=1,
                    permission_mode="bypassPermissions",
                    setting_sources=["user", "project"],
                    model=self._model_name,
                    env={"ANTHROPIC_API_KEY": ""},
                )
                chunks = []
                result_message = None
                async for message in query(prompt=prompt, options=options):
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            text = getattr(block, "text", None)
                            if text:
                                chunks.append(text)
                    elif isinstance(message, ResultMessage):
                        result_message = message
                if result_message is not None and result_message.is_error:
                    raise RuntimeError(
                        f"judge call errored: {result_message.subtype} — {result_message.result}")
                return "\n".join(chunks)

            def generate(self, prompt: str, schema=None) -> str:
                return asyncio.run(self.a_generate(prompt, schema=schema))

    except Exception as exc:  # ImportError, or deepeval blowing up on import
        _import_error = f"{type(exc).__name__}: {exc}"

EVALS_AVAILABLE = _ENABLED and _import_error is None


@dataclass
class EvalOutcome:
    name: str
    score: Optional[float]
    threshold: float
    success: bool
    reason: str


def evals_banner() -> str:
    """One line naming the eval judge state. Printed at startup so evals are never silently off."""
    if not _ENABLED:
        return "[evals] disabled (PIPELINE_EVALS=0 or unset)"
    if _import_error:
        return f"[evals] disabled - deepeval metrics unavailable ({_import_error})"
    return (f"[evals] active -> judge model {_MODEL_NAME} via Claude Agent SDK "
            f"(real usage per call; see PIPELINE_EVAL_MODEL)")


def _judge():
    return _ClaudeAgentSdkModel(_MODEL_NAME)


def _measure(name: str, build) -> EvalOutcome:
    """Call `build()` (which constructs the judge model, the metric, and the test case) then
    measure it, translating any failure into an EvalOutcome instead of raising.

    `build` is deliberately a callable, not a pre-built (metric, test_case) pair: constructing
    `_ClaudeAgentSdkModel` itself can raise (e.g. if `claude_agent_sdk` isn't installed), so
    judge/metric construction must be INSIDE this try, not done by the caller beforehand. A
    missing dependency, an auth failure, a rate limit, or a transient network error must all
    degrade to "eval inconclusive", not take down a stage that otherwise succeeded — the eval is
    a new, unproven signal layered onto a pipeline that already has enough ways to fail.
    """
    try:
        metric, test_case = build()
        metric.measure(test_case)
    except Exception as exc:
        return EvalOutcome(name=name, score=None, threshold=0.5, success=False,
                            reason=f"eval judge call failed: {type(exc).__name__}: {exc}")
    return EvalOutcome(name=name, score=metric.score, threshold=metric.threshold,
                        success=bool(metric.success), reason=metric.reason or "")


def evaluate_generate_groundedness(ticket_id: str, acceptance_criteria: str,
                                    testsuite_text: str) -> Optional[EvalOutcome]:
    """Judge whether stage_generate's test cases are grounded in the ticket's real AC.

    Returns None only when evals are disabled/unavailable (a categorically different case from
    "we tried and it failed") — that distinction matters in the trace: None means "not attempted",
    an EvalOutcome with success=False means "attempted and it did not look grounded" (or the
    judge call itself failed, which is also visible in `reason`).
    """
    if not EVALS_AVAILABLE:
        return None

    if not acceptance_criteria.strip():
        return EvalOutcome(
            name="generate_groundedness", score=None, threshold=0.5, success=False,
            reason="no acceptance-criteria context available (groom stage produced no .md "
                   "artifact) — cannot judge groundedness without it",
        )

    def build():
        metric = _GEval(
            name="Groundedness",
            model=_judge(),
            evaluation_params=[_SingleTurnParams.CONTEXT, _SingleTurnParams.ACTUAL_OUTPUT],
            criteria=(
                "Given the user story's acceptance criteria in 'context', determine whether the "
                "generated test cases in 'actual_output' are grounded in that context: every "
                "screen, field, business rule, validation message, or expected result the test "
                "steps assert should be either explicitly present in the acceptance criteria or "
                "a reasonable, clearly-justifiable elaboration of it (e.g. a standard field "
                "validation any competent tester would add). Penalize test cases that assert "
                "specific UI text, error messages, screen names, or business rules that have no "
                "basis in the acceptance criteria and read as invented."
            ),
        )
        test_case = _LLMTestCase(
            input=f"Ticket {ticket_id} acceptance criteria:\n{acceptance_criteria[:_MAX_CONTEXT_CHARS]}",
            actual_output=testsuite_text[:_MAX_OUTPUT_CHARS],
            context=[acceptance_criteria[:_MAX_CONTEXT_CHARS]],
        )
        return metric, test_case

    return _measure("generate_groundedness", build)


def evaluate_report_summary(source_text: str, summary_text: str) -> Optional[EvalOutcome]:
    """Judge whether qa-report.md faithfully summarizes execution_log.json.

    See evaluate_generate_groundedness's docstring for the None-vs-EvalOutcome(success=False)
    distinction.
    """
    if not EVALS_AVAILABLE:
        return None

    def build():
        metric = _SummarizationMetric(model=_judge())
        test_case = _LLMTestCase(
            input=source_text[:_MAX_OUTPUT_CHARS],
            actual_output=summary_text[:_MAX_CONTEXT_CHARS],
        )
        return metric, test_case

    return _measure("report_summarization", build)
