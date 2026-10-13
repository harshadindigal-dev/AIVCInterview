"""FastAPI app: server-rendered pages (Jinja2 + HTMX)."""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import ValidationError
from sqlmodel import Session, func, select

from app import feedback as fb
from app import metrics as metrics_mod
from app import seed as seed_mod
from app.config import ROOT, get_domain, get_settings
from app.evaluation import RESULTS_DIR, load_dataset, load_results
from app.llm.client import OpenRouterClient
from app.llm.prompts import PROMPT_VERSION
from app.models import Enquiry, Feedback, Notification, PipelineRun, init_db, make_engine
from app.pipeline.enrich import MockCompanyData, MockCRMClient
from app.pipeline.run import STEPS, PipelineDeps, create_enquiry, process_enquiry
from app.schemas import COMPANY_SIZES, COMPLEXITIES, INDUSTRIES, URGENCIES, EnquiryIn

HERE = Path(__file__).resolve().parent


# -- logging: one JSON object per line to stdout --------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        try:
            payload = json.loads(msg)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            payload = {"message": msg}
        out = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "level": record.levelname,
               "logger": record.name, **payload}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure_logging() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_JsonFormatter())
    for name in ("pipeline", "seed", "app"):
        lg = logging.getLogger(name)
        lg.handlers[:] = [h]
        lg.setLevel(logging.INFO)
        lg.propagate = False


log = logging.getLogger("app")

# In-memory progress for the submit page (single process; fine at ~10 enquiries a day).
PROGRESS: dict[str, dict[str, Any]] = {}
_progress_lock = threading.Lock()


def build_deps(settings, domain) -> PipelineDeps:
    client = OpenRouterClient(settings.openrouter_api_key, app_url=settings.openrouter_app_url,
                              app_name=settings.openrouter_app_name, timeout=settings.llm_timeout_seconds)
    return PipelineDeps(client=client, domain=domain, model=settings.triage_model,
                        crm=MockCRMClient(domain.crm), company_data=MockCompanyData(domain.crm),
                        fallback_models=settings.fallback_model_list, backoff_seconds=settings.retry_backoff_seconds,
                        base_url=settings.openrouter_app_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    settings.validate_for_startup()  # fail fast: no key, no app
    domain = get_domain()
    Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True) \
        if settings.database_url.startswith("sqlite:///") else None
    engine = make_engine(settings.database_url)
    init_db(engine)
    app.state.engine = engine
    app.state.deps = build_deps(settings, domain)
    if settings.seed_on_startup and seed_mod.needs_seed(engine):
        log.info(json.dumps({"event": "seed_start", "model": settings.triage_model}))
        seed_mod.start_background_seed(engine, app.state.deps)
    else:
        seed_mod.status.update(state="done")
    log.info(json.dumps({"event": "startup", "model": settings.triage_model, "prompt_version": PROMPT_VERSION}))
    yield


app = FastAPI(title="Intake Triage", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# -- template helpers -------------------------------------------------------------

def _highlight(text: str, entities: list[str] | None) -> Markup:
    out = str(escape(text))
    terms = sorted({str(escape(e)) for e in (entities or []) if e and len(e) >= 3}, key=len, reverse=True)
    terms = [t for t in terms if t.lower() in out.lower()]
    if terms:
        pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
        out = pattern.sub(lambda m: f'<mark>{m.group(0)}</mark>', out)
    return Markup(out)


def _pct(v: float | None, digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _money(v: float | None, digits: int = 2) -> str:
    return "—" if v is None else f"${v:,.{digits}f}"


def _ms(v: int | float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f}s" if v >= 1000 else f"{int(v)}ms"


def _dt(v: datetime | str | None) -> str:
    if v is None:
        return "—"
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.strftime("%d %b %Y, %H:%M")


templates.env.filters.update(highlight=_highlight, pct=_pct, money=_money, ms=_ms, dt=_dt,
                             tojson_pretty=lambda v: json.dumps(v, indent=2, default=str))

STATUS_LABELS = {"processing": "Processing", "auto_routed": "Auto-routed", "in_review": "In review",
                 "accepted": "Accepted", "reassigned": "Reassigned", "archived": "Archived", "duplicate": "Duplicate"}
CASE_LABELS = {"multi_service": "Multi-service", "vague": "Vague", "urgency_mismatch": "Urgency mismatch",
               "small_company_complex": "Small company, complex problem", "existing_client": "Existing client",
               "conflict": "Conflict flag", "spam": "Spam", "job_application": "Job application",
               "vendor_pitch": "Vendor pitch", "out_of_scope": "Out of scope", "prompt_injection": "Prompt injection",
               "neighbour_risk_vs_audit": "Confusable: risk vs audit",
               "neighbour_tech_vs_strategy": "Confusable: tech vs strategy", "duplicate": "Duplicate submission"}
RULE_LABELS = {"hard_stop_review": "Hard stop", "archive_not_an_enquiry": "Not an enquiry",
               "existing_client": "Existing client", "review_flags": "Review triggers",
               "complex_high_urgency": "Complex + high urgency", "default": "Service-line lead", "dedupe": "Duplicate"}


def session(request: Request) -> Session:
    return Session(request.app.state.engine, expire_on_commit=False)


def review_count(s: Session) -> int:
    return s.exec(select(func.count()).select_from(Enquiry).where(Enquiry.status == "in_review")).one()


def render(request: Request, name: str, status_code: int = 200, **ctx: Any) -> HTMLResponse:
    settings = get_settings()
    with session(request) as s:
        rc = review_count(s)
    base = dict(request=request, mode="LIVE", model=settings.triage_model, review_count=rc,
                seed=dict(seed_mod.status), domain=get_domain(), status_labels=STATUS_LABELS,
                rule_labels=RULE_LABELS, case_labels=CASE_LABELS, nav=ctx.pop("nav", ""))
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


# -- health ------------------------------------------------------------------------

@app.get("/healthz")
def healthz(request: Request):
    with session(request) as s:
        s.exec(select(func.count()).select_from(Enquiry)).one()
    return {"ok": True, "seed": seed_mod.status["state"]}


# -- submit --------------------------------------------------------------------------

def _samples() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"Clear cases": [], "Hard cases": []}
    for r in load_dataset():
        g = r["gold"]
        if g["case_type"] == "clear":
            label = f"{r['company_name']}"
            groups["Clear cases"].append({"id": r["id"], "label": label, **{k: r[k] for k in (
                "name", "email", "company_name", "industry", "company_size", "stated_urgency", "description")}})
        else:
            label = f"{CASE_LABELS.get(g['case_type'], g['case_type'])} — {r['company_name']}"
            groups["Hard cases"].append({"id": r["id"], "label": label, "case": g["case_type"], **{k: r[k] for k in (
                "name", "email", "company_name", "industry", "company_size", "stated_urgency", "description")}})
    groups["Hard cases"].sort(key=lambda x: x["label"])
    return groups


@app.get("/", response_class=HTMLResponse)
def submit_page(request: Request):
    return render(request, "submit.html", nav="submit", samples=_samples(), industries=INDUSTRIES,
                  sizes=COMPANY_SIZES, urgencies=URGENCIES, values={}, errors={})


def _run_in_background(engine, deps: PipelineDeps, enquiry_id: str) -> None:
    def on_step(step: str, status: str, info: dict) -> None:
        with _progress_lock:
            PROGRESS[enquiry_id]["steps"][step] = status
            if info.get("error"):
                PROGRESS[enquiry_id]["notes"].append(info["error"])

    def target() -> None:
        try:
            with Session(engine, expire_on_commit=False) as s:
                enq = s.get(Enquiry, enquiry_id)
                process_enquiry(s, enq, deps, on_step=on_step)
        except Exception as e:  # never drop an enquiry: park it in review
            log.exception("pipeline crashed")
            with Session(engine, expire_on_commit=False) as s:
                enq = s.get(Enquiry, enquiry_id)
                enq.status, enq.destination_type, enq.destination_key = "in_review", "review", "review"
                enq.destination_name = "Review queue"
                enq.review_reasons = [f"Pipeline error: {type(e).__name__}"]
                s.add(enq)
                s.commit()
            with _progress_lock:
                PROGRESS[enquiry_id]["notes"].append(f"Pipeline error: {e}")
        finally:
            with _progress_lock:
                PROGRESS[enquiry_id]["finished"] = True

    threading.Thread(target=target, daemon=True, name=f"pipeline-{enquiry_id}").start()


@app.post("/submit", response_class=HTMLResponse)
def submit(request: Request, name: str = Form(""), email: str = Form(""), company_name: str = Form(""),
           industry: str = Form(""), company_size: str = Form(""), stated_urgency: str = Form(""),
           description: str = Form("")):
    values = dict(name=name, email=email, company_name=company_name, industry=industry, company_size=company_size,
                  stated_urgency=stated_urgency, description=description)
    try:
        data = EnquiryIn(**values)
    except ValidationError as e:
        errors = {}
        for err in e.errors():
            field = str(err["loc"][0]) if err["loc"] else "form"
            msg = err["msg"].removeprefix("Value error, ")
            if err["type"] == "literal_error":
                msg = "choose an option"
            errors.setdefault(field, msg)
        return render(request, "_submit_form.html", samples=_samples(), industries=INDUSTRIES, sizes=COMPANY_SIZES,
                      urgencies=URGENCIES, values=values, errors=errors)
    deps = request.app.state.deps
    with session(request) as s:
        enq = create_enquiry(s, data, deps)
    with _progress_lock:
        PROGRESS[enq.id] = {"steps": {"intake": "running", **{st: "pending" for st in STEPS[1:]}},
                            "notes": [], "finished": False}
    _run_in_background(request.app.state.engine, deps, enq.id)
    return render(request, "_progress.html", enquiry_id=enq.id, progress=PROGRESS[enq.id], steps=STEPS)


@app.get("/enquiries/{enquiry_id}/progress", response_class=HTMLResponse)
def progress(request: Request, enquiry_id: str):
    with _progress_lock:
        p = json.loads(json.dumps(PROGRESS.get(enquiry_id) or {}))
    if not p or p.get("finished"):
        return Response(status_code=200, headers={"HX-Redirect": f"/enquiries/{enquiry_id}"})
    return render(request, "_progress.html", enquiry_id=enquiry_id, progress=p, steps=STEPS)


# -- detail ---------------------------------------------------------------------------

def _load_detail(s: Session, enquiry_id: str) -> dict[str, Any]:
    enq = s.get(Enquiry, enquiry_id)
    if enq is None:
        raise HTTPException(404, "Enquiry not found")
    run = s.get(PipelineRun, enq.latest_run_id) if enq.latest_run_id else None
    feedback = s.exec(select(Feedback).where(Feedback.enquiry_id == enquiry_id).order_by(Feedback.created_at)).all()
    notes = s.exec(select(Notification).where(Notification.enquiry_id == enquiry_id)
                   .order_by(Notification.created_at)).all()
    duplicates = s.exec(select(Enquiry).where(Enquiry.duplicate_of == enquiry_id)).all()
    return dict(e=enq, run=run, feedback=feedback, notifications=notes, duplicates=duplicates)


@app.get("/enquiries/{enquiry_id}", response_class=HTMLResponse)
def detail(request: Request, enquiry_id: str, action: str | None = None, done: str | None = None):
    domain = get_domain()
    with session(request) as s:
        d = _load_detail(s, enquiry_id)
    e = d["e"]
    suggested = fb.suggested_lead(domain, e)
    return render(request, "detail.html", nav="inbox", action=action, done=done, suggested=suggested,
                  leads=domain.all_leads(), complexities=COMPLEXITIES, **d)


# -- actions ----------------------------------------------------------------------------

def _after_action(request: Request, enquiry_id: str, context: str, done: str) -> Response:
    if context == "review":
        with session(request) as s:
            rc = review_count(s)
        html = f'<span id="review-count" class="badge" hx-swap-oob="true">{rc}</span>'
        if rc == 0:
            html += ('<div id="review-empty" hx-swap-oob="true" class="empty">'
                     '<p class="empty-title">Nothing waiting for review.</p>'
                     '<p>Flagged enquiries will appear here.</p></div>')
        return HTMLResponse(html)
    return Response(status_code=200, headers={"HX-Redirect": f"/enquiries/{enquiry_id}?done={done}"})


@app.post("/enquiries/{enquiry_id}/accept")
def action_accept(request: Request, enquiry_id: str, context: str = Form("detail")):
    domain = get_domain()
    with session(request) as s:
        e = s.get(Enquiry, enquiry_id) or _404()
        if e.status == "in_review":
            fb.accept_suggestion_from_review(s, domain, e, request.app.state.deps.base_url)
        else:
            fb.accept(s, e)
    return _after_action(request, enquiry_id, context, "accepted")


@app.post("/enquiries/{enquiry_id}/reassign")
def action_reassign(request: Request, enquiry_id: str, lead_key: str = Form(...), reason: str = Form(""),
                    context: str = Form("detail")):
    domain = get_domain()
    if lead_key not in domain.routing["team_leads"]:
        raise HTTPException(400, "Unknown team lead")
    with session(request) as s:
        e = s.get(Enquiry, enquiry_id) or _404()
        fb.reassign(s, domain, e, lead_key, reason.strip(), request.app.state.deps.base_url)
    return _after_action(request, enquiry_id, context, "reassigned")


@app.post("/enquiries/{enquiry_id}/misclassified")
def action_misclassified(request: Request, enquiry_id: str, service_line: str = Form(...),
                         complexity: str = Form(...), reason: str = Form(""), context: str = Form("detail")):
    domain = get_domain()
    if service_line not in domain.outcome_ids or complexity not in COMPLEXITIES:
        raise HTTPException(400, "Unknown service line or complexity")
    with session(request) as s:
        e = s.get(Enquiry, enquiry_id) or _404()
        fb.mark_misclassified(s, domain, e, service_line, complexity, reason.strip(), request.app.state.deps.base_url)
    return _after_action(request, enquiry_id, context, "corrected")


def _404():
    raise HTTPException(404, "Enquiry not found")


# -- inbox / review ------------------------------------------------------------------------

@app.get("/enquiries", response_class=HTMLResponse)
def inbox(request: Request, service_line: str = "", status: str = "", destination: str = ""):
    with session(request) as s:
        q = select(Enquiry).order_by(Enquiry.submitted_at.desc())
        if service_line:
            q = q.where(Enquiry.service_line == service_line)
        if status:
            q = q.where(Enquiry.status == status)
        if destination:
            q = q.where(Enquiry.destination_name == destination)
        rows = s.exec(q).all()
        destinations = sorted({d for d in s.exec(select(Enquiry.destination_name)).all() if d})
    filters = dict(service_line=service_line, status=status, destination=destination)
    if is_htmx(request) and request.headers.get("HX-Target") == "inbox-table":
        return render(request, "_inbox_table.html", rows=rows, filters=filters)
    return render(request, "inbox.html", nav="inbox", rows=rows, filters=filters, destinations=destinations)


@app.get("/review", response_class=HTMLResponse)
def review(request: Request):
    domain = get_domain()
    with session(request) as s:
        rows = s.exec(select(Enquiry).where(Enquiry.status == "in_review").order_by(Enquiry.submitted_at.desc())).all()
    items = [{"e": e, "suggested": fb.suggested_lead(domain, e)} for e in rows]
    return render(request, "review.html", nav="review", items=items, leads=domain.all_leads())


# -- evaluation ---------------------------------------------------------------------------

def _md(text: str) -> Markup:
    """Tiny markdown subset for failure_notes.md: headings, bullets, paragraphs, **bold**, `code`."""
    html, para, items = [], [], []

    def inline(s: str) -> str:
        s = str(escape(s))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        return re.sub(r"`(.+?)`", r"<code>\1</code>", s)

    def flush():
        if para:
            html.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            html.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        st = line.strip()
        if not st:
            flush()
        elif st.startswith("#"):
            flush()
            level = min(len(st) - len(st.lstrip("#")) + 1, 4)
            html.append(f"<h{level}>{inline(st.lstrip('#').strip())}</h{level}>")
        elif st.startswith(("- ", "* ")):
            if para:
                flush()
            items.append(st[2:])
        elif items and line.startswith("  "):
            items[-1] += " " + st
        else:
            para.append(st)
    flush()
    return Markup("\n".join(html))


@app.get("/eval", response_class=HTMLResponse)
def eval_page(request: Request, model: str = ""):
    settings = get_settings()
    results = load_results()
    order = {m: i for i, m in enumerate(settings.all_models)}
    results.sort(key=lambda r: (order.get(r["model"], 99), r["model"]))
    selected = next((r for r in results if r["model"] == model), None) or next(
        (r for r in results if r["model"] == settings.triage_model), results[0] if results else None)
    notes_path = RESULTS_DIR / "failure_notes.md"
    notes = _md(notes_path.read_text()) if notes_path.exists() else None
    with session(request) as s:
        existing = set(s.exec(select(Enquiry.id)).all())
    return render(request, "eval.html", nav="eval", results=results, selected=selected, notes=notes,
                  existing=existing)


# -- notifications / metrics ------------------------------------------------------------------

@app.get("/notifications", response_class=HTMLResponse)
def notifications(request: Request, limit: int = 100):
    with session(request) as s:
        rows = s.exec(select(Notification).order_by(Notification.created_at.desc()).limit(limit)).all()
        total = s.exec(select(func.count()).select_from(Notification)).one()
    return render(request, "notifications.html", nav="notifications", rows=rows, total=total)


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request):
    with session(request) as s:
        m = metrics_mod.compute(s, get_domain())
    return render(request, "metrics.html", nav="metrics", m=m)


@app.get("/metrics.json")
def metrics_json(request: Request):
    with session(request) as s:
        return JSONResponse(metrics_mod.compute(s, get_domain()))


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return render(request, "error.html", status_code=404, title="Not found",
                  message=getattr(exc, "detail", "Page not found"))
