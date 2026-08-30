"""Local backend for the QA pipeline dashboard: a JSON API + static file server.

No external dependencies (stdlib http.server only) — nothing to install beyond Python itself to
run the backend. Serves:

- `POST /save` + `GET /config` — the pipeline.config.json read/write pair the dashboard's
  Configure page uses (same contract as the old pipelines/configure.py's form).
- `GET /api/traces`, `GET /api/trace` — trace.jsonl listing/reading for the Traces page's
  waterfall (unchanged from pipelines/configure.py).
- `GET /api/tickets`, `GET /api/tickets/<id>` — one row/detail per ticket, assembled from every
  stage's own summary.json (or testsuite.json) under bunker/, so the dashboard can show a whole
  pipeline run without anyone hand-parsing files under bunker/.
- `GET /api/tickets/<id>/screenshots/<file>` — serves execution-evidence screenshots referenced
  by the execute stage's summary.json.
- Everything else — serves dashboard/dist/ (the built React app), falling back to index.html so
  client-side routing survives a hard refresh. Prints a clear message instead of a bare 404 if
  the dashboard hasn't been built yet.

Usage:
    python3 -m pipelines.api [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from pipelines.common import REPO_ROOT, ARTIFACTS_ROOT

CONFIG_PATH = REPO_ROOT / "pipeline.config.json"
EXAMPLE_PATH = REPO_ROOT / "pipeline.config.example.json"
BUNKER_ROOT = REPO_ROOT / "bunker"
DASHBOARD_DIST = REPO_ROOT / "dashboard" / "dist"

_URL_RE = re.compile(r"^https?://\S+$")

# One row per pipeline stage: where its artifacts live under bunker/, and how to find the one
# canonical JSON file among whatever else that stage's agent also writes (MD/HTML/PDF/CSV).
# Directory names match what the current agent specs document (see docs/CONFIGURATION.md and
# each agent's own file) - NOT necessarily pipelines/flow.py's older GROOM_DIRS constants, which
# predate this and are tracked separately (see flow.py's own comments on that history).
_STAGES = {
    "groom": {"dir": "story-analysis-reports", "summary_glob": "*-evaluation-report.summary.json"},
    "pr_impact": {"dir": "pr-analysis-reports", "summary_glob": "summary.json"},
    "cross_system": {"dir": "cct-impact-reports", "summary_glob": "summary.json"},
    "generate": {"dir": "test-case-reports", "summary_glob": "*-tests.testsuite.json"},
    "execute": {"dir": "manual-test-execution", "summary_glob": "*-summary.json"},
}

# Stage directories are named "<TYPE>_<ticket_id>" (e.g. "US_2928495", "BUG_2928495") - a ticket
# is identified by the id after the underscore, independent of which work-item type it is.
_TICKET_DIR_RE = re.compile(r"^(?P<type>[A-Za-z]+)_(?P<id>.+)$")


def _load_current_config() -> dict:
    """Prefill the config form from an existing pipeline.config.json, falling back to the example."""
    for path in (CONFIG_PATH, EXAMPLE_PATH):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def validate(config: dict) -> list:
    """Basic shape validation - required fields, URL format, no duplicate app names.

    Deliberately not deep schema validation: this is a QA-facing form, not an API boundary. The
    goal is to catch typos (an empty project name, a malformed URL) before they turn into a
    confusing failure three stages into a pipeline run - not to police every possible key.
    """
    errors = []

    project = config.get("project") or {}
    if not str(project.get("name") or "").strip():
        errors.append("Project name is required.")

    backend = config.get("backend") or {}
    if not str(backend.get("type") or "").strip():
        errors.append("Backend type is required.")

    apps = config.get("apps") or []
    seen_names = set()
    for i, app in enumerate(apps, start=1):
        name = str(app.get("name") or "").strip()
        if not name:
            errors.append(f"App #{i} is missing a name.")
        elif name.lower() in seen_names:
            errors.append(f"Duplicate app name: {name!r}.")
        else:
            seen_names.add(name.lower())
        for env, url in (app.get("urls") or {}).items():
            url = str(url or "").strip()
            if url and not _URL_RE.match(url):
                errors.append(
                    f"App {name!r}, environment {env!r}: {url!r} does not look like a valid "
                    "http(s) URL."
                )

    db = config.get("database") or {}
    if db and not str(db.get("connectionStringEnv") or "").strip():
        errors.append(
            "Database section is filled in but 'connection string env var' is empty - clear "
            "the whole section if you don't need backend verification."
        )

    return errors


def _list_traces() -> list:
    """One entry per ticket that has ever produced a trace.jsonl, newest-modified first."""
    out = []
    if not ARTIFACTS_ROOT.exists():
        return out
    for ticket_dir in ARTIFACTS_ROOT.iterdir():
        trace_file = ticket_dir / "trace.jsonl"
        if not trace_file.is_file():
            continue
        try:
            lines = [l for l in trace_file.read_text(encoding="utf-8").splitlines() if l.strip()]
            mtime = trace_file.stat().st_mtime
        except OSError:
            continue
        out.append({"ticket_id": ticket_dir.name, "runs": len(lines), "mtime": mtime})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def _read_trace(ticket_id: str, run: int) -> dict:
    """Return the parsed trace for one run (run=-1 is the most recent), or {} if not found."""
    trace_file = ARTIFACTS_ROOT / ticket_id / "trace.jsonl"
    if not trace_file.is_file():
        return {}
    lines = [l for l in trace_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return {}
    try:
        line = lines[run]
    except IndexError:
        line = lines[-1]
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def _find_stage_dir(stage: str, ticket_id: str) -> Path | None:
    """Find the "<TYPE>_<ticket_id>" directory for one stage, or None if that stage hasn't run.

    Tries an exact "<TYPE>_<ticket_id>" match first (cheap, no false positives from a ticket id
    that happens to be a substring of another), then falls back to a suffix glob for callers that
    already know the bare id but not its work-item type.
    """
    stage_root = BUNKER_ROOT / _STAGES[stage]["dir"]
    if not stage_root.exists():
        return None
    for child in stage_root.iterdir():
        if not child.is_dir():
            continue
        m = _TICKET_DIR_RE.match(child.name)
        if m and m.group("id") == ticket_id:
            return child
    return None


def _read_stage_summary(ticket_id: str, stage: str) -> dict:
    """Locate and parse the one canonical JSON file for a stage, or {} if that stage hasn't run."""
    stage_dir = _find_stage_dir(stage, ticket_id)
    if stage_dir is None:
        return {}
    matches = sorted(stage_dir.glob(_STAGES[stage]["summary_glob"]))
    if not matches:
        return {}
    try:
        return json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_qa_report(ticket_id: str) -> str:
    path = ARTIFACTS_ROOT / ticket_id / "qa-report.md"
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _compute_status(stages: dict) -> str:
    """One headline status per ticket, preferring the most execution-grounded signal available.

    Order matters: an actual execution pass rate is more trustworthy than a pre-execution risk
    estimate, which in turn is more informative than "nothing has run yet".
    """
    execute = stages.get("execute") or {}
    summary = execute.get("summary") or {}
    if summary.get("totalTCs") is not None:
        failed = summary.get("failed") or 0
        blocked = summary.get("blocked") or 0
        if failed or blocked:
            return "fail"
        if summary.get("totalTCs"):
            return "pass"

    cross_system = stages.get("cross_system") or {}
    verdict = cross_system.get("verdict") or {}
    if "safeToRelease" in verdict:
        return "pass" if verdict["safeToRelease"] else "fail"

    pr_impact = stages.get("pr_impact") or {}
    ship_risk = (pr_impact.get("shipRisk") or "").lower()
    if ship_risk:
        return "fail" if ship_risk in ("high", "critical") else "pass" if ship_risk == "low" else "pending"

    if stages:
        return "pending"
    return "unknown"


def _list_tickets() -> list:
    """One row per ticket id seen across any stage's bunker/ output or artifacts/flow/, newest first."""
    seen = {}  # ticket_id -> {stages: set(), mtime: float, type: str}

    for stage, cfg in _STAGES.items():
        stage_root = BUNKER_ROOT / cfg["dir"]
        if not stage_root.exists():
            continue
        for child in stage_root.iterdir():
            if not child.is_dir():
                continue
            m = _TICKET_DIR_RE.match(child.name)
            if not m:
                continue
            ticket_id = m.group("id")
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = 0
            entry = seen.setdefault(ticket_id, {"stages": set(), "mtime": 0.0, "type": m.group("type")})
            entry["stages"].add(stage)
            entry["mtime"] = max(entry["mtime"], mtime)

    if ARTIFACTS_ROOT.exists():
        for ticket_dir in ARTIFACTS_ROOT.iterdir():
            if not ticket_dir.is_dir():
                continue
            ticket_id = ticket_dir.name
            has_report = (ticket_dir / "qa-report.md").is_file()
            has_trace = (ticket_dir / "trace.jsonl").is_file()
            if not (has_report or has_trace):
                continue
            entry = seen.setdefault(ticket_id, {"stages": set(), "mtime": 0.0, "type": None})
            if has_report:
                entry["stages"].add("report")
            if has_trace:
                entry["stages"].add("trace")
            try:
                entry["mtime"] = max(entry["mtime"], ticket_dir.stat().st_mtime)
            except OSError:
                pass

    out = []
    for ticket_id, entry in seen.items():
        stages_present = sorted(entry["stages"])
        # _compute_status reads stages["execute"]["summary"] - execute's own summary.json
        # already nests its rollup under a "summary" key (see the mte-2.0 schema), so passing
        # the raw parsed file straight through matches what _compute_status expects, same as
        # _ticket_detail() does. Only fetch the stages that inform status, skip the rest for a
        # fast list view.
        stages_data = {
            s: _read_stage_summary(ticket_id, s)
            for s in ("execute", "cross_system", "pr_impact") if s in stages_present
        }
        out.append({
            "ticket_id": ticket_id,
            "type": entry["type"],
            "stages": stages_present,
            "mtime": entry["mtime"],
            "status": _compute_status(stages_data),
        })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def _ticket_detail(ticket_id: str) -> dict:
    stages = {}
    for stage in _STAGES:
        data = _read_stage_summary(ticket_id, stage)
        if data:
            stages[stage] = data
    return {
        "ticket_id": ticket_id,
        "stages": stages,
        # `stages` already holds each stage's raw parsed JSON (execute's own file already nests
        # its rollup under "summary" per the mte-2.0 schema), which is exactly the shape
        # _compute_status expects - no re-wrapping needed.
        "status": _compute_status(stages),
        "qa_report_md": _read_qa_report(ticket_id),
    }


def _screenshot_path(ticket_id: str, filename: str) -> Path | None:
    """Resolve one execution-evidence screenshot, refusing anything that isn't a plain filename."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    stage_dir = _find_stage_dir("execute", ticket_id)
    if stage_dir is None:
        return None
    candidate = stage_dir / "screenshots" / filename
    return candidate if candidate.is_file() else None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default per-request stderr logging - this is a local single-user tool

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str = None) -> None:
        body = path.read_bytes()
        ctype = content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_dashboard_not_built(self) -> None:
        msg = (
            "<h1>Dashboard not built yet</h1>"
            "<p>Run <code>cd dashboard &amp;&amp; npm install &amp;&amp; npm run build</code>, "
            "then reload this page.</p>"
        )
        body = msg.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self, path: str) -> None:
        """Static file serving for the built React app, with an SPA fallback to index.html."""
        if not DASHBOARD_DIST.exists():
            self._send_dashboard_not_built()
            return
        rel = path.lstrip("/")
        candidate = (DASHBOARD_DIST / rel) if rel else (DASHBOARD_DIST / "index.html")
        if candidate.is_file():
            self._send_file(candidate)
            return
        index = DASHBOARD_DIST / "index.html"
        if index.is_file():
            self._send_file(index, content_type="text/html; charset=utf-8")
        else:
            self._send_dashboard_not_built()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        segments = [unquote(s) for s in path.strip("/").split("/") if s]

        if path == "/config":
            self._send_json(200, _load_current_config())
        elif path == "/api/traces":
            self._send_json(200, {"traces": _list_traces()})
        elif path == "/api/trace":
            ticket_id = (query.get("ticket") or [""])[0]
            run = int((query.get("run") or ["-1"])[0])
            trace = _read_trace(ticket_id, run) if ticket_id else {}
            self._send_json(200, {"trace": trace})
        elif segments[:2] == ["api", "tickets"] and len(segments) == 2:
            self._send_json(200, {"tickets": _list_tickets()})
        elif segments[:2] == ["api", "tickets"] and len(segments) == 3:
            self._send_json(200, _ticket_detail(segments[2]))
        elif segments[:2] == ["api", "tickets"] and len(segments) == 5 and segments[3] == "screenshots":
            img = _screenshot_path(segments[2], segments[4])
            if img is None:
                self.send_response(404)
                self.end_headers()
            else:
                self._send_file(img)
        elif path.startswith("/api/"):
            self.send_response(404)
            self.end_headers()
        else:
            self._serve_dashboard(path)

    def do_POST(self):
        if self.path != "/save":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            config = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"ok": False, "errors": [f"Malformed JSON from the form: {exc}"]})
            return

        errors = validate(config)
        if errors:
            self._send_json(400, {"ok": False, "errors": errors})
            return

        CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self._send_json(200, {"ok": True, "path": str(CONFIG_PATH)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open a browser tab.")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), _Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"[api] serving {url} - press Ctrl+C to stop")
    print(f"[api] dashboard at {url}/  (build it first: cd dashboard && npm install && npm run build)")
    print(f"[api] config writes to {CONFIG_PATH}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[api] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
