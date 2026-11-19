"""CareBridge's public site, authenticated portals, and live agent transport."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import replace
from urllib.parse import urlencode, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

from .agent_catalog import agent_catalog, agent_overview
from .auth import Accounts, AuthError, SESSION_LIFETIME
from .clinic_db import get_patient, set_fault
from .clinic_tools import ClinicService
from .config import ROOT, get_settings
from .doctor_schedule import DEPARTMENTS, WEEKDAYS, DoctorSchedule, ScheduleError
from .rag import load_documents
from .runtime import Runtime
from .web_auth import WebAuth
from .web_session import WebSession

WEB_DIR = ROOT / "web"


def create_app(settings=None, llm=None, mail=None):
    settings = settings or get_settings()
    templates = Environment(loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=select_autoescape())
    accounts = Accounts(settings.clinic_db)
    clinic = ClinicService(settings.clinic_db, settings.clinic_now)
    schedule = DoctorSchedule(settings.clinic_db, settings.clinic_now)
    state = {}
    world_lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        async with Runtime(settings, llm=llm) as rt:
            accounts.prepare()
            state["runtime"] = rt
            yield

    def render(request, template, title, *, status=200, **fields):
        csrf = auth.csrf(request)
        user = auth.user(request)
        html = templates.get_template(template).render(
            title=title, path=request.url.path, csrf=csrf, user=user, today=settings.clinic_now.date().isoformat(),
            **fields,
        )
        response = HTMLResponse(html, status)
        if request.cookies.get(auth.csrf_cookie) != csrf:
            auth.set_cookie(response, auth.csrf_cookie, csrf, SESSION_LIFETIME)
        return response

    auth = WebAuth(settings, accounts, render, mail=mail)

    def require(request, role=None):
        user = auth.user(request)
        if not user:
            target = "/book" + (f"?{request.url.query}" if request.url.query else "")
            url = "/sign-in?" + urlencode({"next": target}) if request.url.path == "/book" else "/sign-in"
            return RedirectResponse(url, 303)
        if role and user["role"] != role:
            return render(request, "message.html", "Access restricted", status=403,
                          message="This page is available from the corresponding clinic account.")
        return user

    async def home(request):
        return render(request, "home.html", "Book a clinic visit")

    async def doctors(request):
        return render(request, "doctors.html", "Doctors", doctors=clinic.list_doctors()["doctors"],
                      departments=DEPARTMENTS)

    async def information(request):
        docs = load_documents(settings.kb_dir)
        return render(request, "information.html", "Clinic information", docs=docs)

    async def patient_pages(request):
        user = require(request, "patient")
        if not isinstance(user, dict):
            return user
        path = request.url.path
        if path in ("/appointments", "/dashboard"):
            visits = clinic.list_patient_appointments(user["patient_id"], include_past=True)["appointments"]
            upcoming = [item for item in visits if item["status"] == "confirmed"
                        and datetime_string(item) >= settings.clinic_now.isoformat(timespec="minutes")]
            history = [item for item in visits if item not in upcoming]
            title = "Your dashboard" if path == "/dashboard" else "Appointments"
            return render(request, "appointments.html", title, upcoming=upcoming, history=history,
                          dashboard=path == "/dashboard", now=settings.clinic_now.isoformat(timespec="minutes"))
        if path == "/book":
            selection = {key: request.query_params.get(key, "") for key in ("department", "doctor_id", "date")}
            doctor_list = clinic.list_doctors()["doctors"]
            for doctor in doctor_list:
                if doctor["doctor_id"] == selection["doctor_id"]:
                    selection["department"] = doctor["department"]
            return render(request, "book.html", "Book a visit", doctors=doctor_list, departments=DEPARTMENTS,
                          selection=selection, last_day=schedule.last_day.isoformat(),
                          new_patient=get_patient(settings.clinic_db, user["patient_id"])["is_new"])
        return render(request, "assistant.html", "Appointment assistant", catalog=agent_catalog())

    async def account(request):
        user = require(request)
        if not isinstance(user, dict):
            return user
        store = state["runtime"].ltm
        error = ""
        if request.method == "POST":
            form = await request.form(max_fields=4, max_part_size=1024)
            try:
                auth.validate_post(request, form.get("csrf"))
                if user["role"] == "patient":
                    key = str(form.get("key", ""))
                    if not key or key not in {fact["key"] for fact in store.facts(user["patient_id"])}:
                        raise AuthError("That preference is no longer saved.")
                    patient = get_patient(settings.clinic_db, user["patient_id"])
                    agent = await state["runtime"].new_agent(patient, None)
                    await agent.respond_action({"action": "forget", "key": key})
                    return RedirectResponse("/account", 303)
            except AuthError as exc:
                error = str(exc)
        prefs = store.facts(user["patient_id"]) if user["role"] == "patient" else []
        return render(request, "account.html", "Account", preferences=prefs, error=error)

    async def doctor_pages(request):
        user = require(request, "doctor")
        if not isinstance(user, dict):
            return user
        doctor_id = user["doctor_id"]
        if request.url.path.endswith("/appointments"):
            return render(request, "doctor_appointments.html", "My appointments", visits=schedule.appointments(doctor_id))

        day = request.query_params.get("date") or schedule.first_day.isoformat()
        notice, error = "", ""
        status = 200
        try:
            if request.method == "POST":
                form = await request.form(max_fields=120, max_part_size=2048)
                auth.validate_post(request, form.get("csrf"))
                day = str(form.get("date", day))
                if form.get("operation") == "hours":
                    values = zip(form.getlist("weekday"), form.getlist("start"), form.getlist("end"), strict=True)
                    rows = [{"weekday": int(weekday), "start_time": start, "end_time": end}
                            for weekday, start, end in values]
                    outcome = schedule.set_weekly_hours(doctor_id, rows)
                    n = outcome["kept"]
                    notice = "Weekly hours saved." + (f" {n} confirmed appointment{'s remain' if n != 1 else ' remains'} outside these hours." if n else "")
                    async with world_lock:
                        state["runtime"].society.availability_changed(doctor_id)
                elif form.get("operation") in {"open", "block"}:
                    schedule.set_slot(doctor_id, day, str(form.get("start_time", "")), form["operation"] == "open")
                    notice = "Slot availability updated."
                    async with world_lock:
                        state["runtime"].society.availability_changed(doctor_id, day)
                else:
                    raise ScheduleError("Choose an availability action.")
            data = schedule.availability(doctor_id, day)
        except (AuthError, ValueError) as exc:
            error = str(exc) if isinstance(exc, (AuthError, ScheduleError)) else "Check your time ranges."
            status = 400
            try:
                data = schedule.availability(doctor_id, day)
            except ScheduleError:
                day = schedule.first_day.isoformat()
                data = schedule.availability(doctor_id, day)
        return render(request, "availability.html", "Availability", status=status, date=day, weekdays=WEEKDAYS,
                      last_day=schedule.last_day.isoformat(), notice=notice, error=error, **data)

    async def agent_pages(request):
        user = require(request)
        if not isinstance(user, dict):
            return user
        rt = state["runtime"]
        if request.url.path == "/knowledge":
            return render(request, "knowledge.html", "Knowledge", threshold=rt.kb.min_score,
                          docs=load_documents(settings.kb_dir))
        if request.url.path == "/simulation":
            return render(request, "simulation.html", "Simulation",
                          enabled=settings.simulation_enabled and user["role"] == "patient")
        return render(request, "agents.html", "Agents", overview=await dashboard_overview(), catalog=agent_catalog())

    async def dashboard_overview():
        async with world_lock:
            return agent_overview(state["runtime"].society)

    async def agents_overview_route(request):
        user = auth.user(request)
        if not user:
            return JSONResponse({"message": "Sign in to view agent activity."}, 401)
        return JSONResponse(await dashboard_overview())

    async def kb_search(request):
        if not auth.user(request):
            return JSONResponse({"message": "Sign in to inspect policy evidence."}, 401)
        kb = state["runtime"].kb
        query = request.query_params.get("q", "").strip()[:400]
        try:
            results = await asyncio.to_thread(kb.scored, query)
        except Exception:
            return JSONResponse({"message": "The policy index is unavailable. Try again shortly."}, 503)
        return JSONResponse({"query": query, "threshold": kb.min_score, "results": results})

    async def simulation(request):
        user = auth.user(request)
        if not user or user["role"] != "patient" or not settings.simulation_enabled:
            return JSONResponse({"message": "Simulation controls are unavailable."}, 403)
        try:
            auth.validate_post(request, request.headers.get("x-csrf-token"))
            body = await request.json()
            event = body.get("event")
            rt = state["runtime"]
            async with world_lock:
                if event == "race":
                    set_fault(settings.clinic_db, f"race:{user['patient_id']}", 1)
                    return JSONResponse({"message": "The next booking for this account will encounter a slot conflict."})
                if event == "doctor_leave":
                    result = rt.society.apply_doctor_leave(user["patient_id"])
                    if not result:
                        return JSONResponse({"message": "You need a confirmed visit before simulating doctor leave."}, 409)
                    return JSONResponse({"message": f"{result['doctor']} is on leave on {result['date']}.",
                                         "appointments_cancelled": result["appointments_cancelled"]})
                if event == "run":
                    ticks = min(40, max(1, int(body.get("ticks", 8))))
                    size = min(20, max(2, int(body.get("patients", 6))))
                    result = await asyncio.to_thread(rt.society.run_day, ticks, extra_patients=size)
                    return JSONResponse({"message": f"Simulation completed {ticks} ticks.", "log": result["log"],
                                         "environment": result["snapshot"]["environment"]})
                raise ValueError
        except AuthError as exc:
            return JSONResponse({"message": str(exc)}, 403)
        except (ValueError, TypeError, AttributeError):
            return JSONResponse({"message": "Check the simulation settings and try again."}, 400)

    async def ws_endpoint(ws):
        if ws.headers.get("origin") != settings.site_origin:
            await ws.close(code=1008)
            return
        user = auth.user(ws)
        if not user or user["role"] != "patient":
            await ws.close(code=1008)
            return
        patient = get_patient(settings.clinic_db, user["patient_id"])
        rt = state["runtime"]
        session = WebSession(agent_factory=rt.new_agent, mcp=rt.mcp, ltm=rt.ltm, patient=patient,
                             log_dir=settings.log_dir)
        await ws.accept()

        async def pump():
            while True:
                await ws.send_json(await session.out.get())

        pump_task = asyncio.create_task(pump())
        try:
            await session.start()
            while auth.user(ws):
                try:
                    event = await asyncio.wait_for(ws.receive_json(), timeout=2)
                except asyncio.TimeoutError:
                    continue
                if not auth.user(ws):
                    break
                if isinstance(event, dict):
                    await session.handle(event)
            await ws.close(code=1008)
        except (WebSocketDisconnect, ValueError, RuntimeError):
            pass
        finally:
            await session.close()
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await pump_task

    async def page_not_found(request, _exc):
        return render(request, "message.html", "Page not found", status=404,
                      message="Check the address or return to the clinic homepage.")

    app = Starlette(
        routes=[
            Route("/", home), Route("/doctors", doctors), Route("/clinic", information),
            *(Route(path, patient_pages) for path in ("/dashboard", "/appointments", "/book", "/assistant")),
            Route("/account", account, methods=["GET", "POST"]),
            Route("/doctor/appointments", doctor_pages),
            Route("/doctor/availability", doctor_pages, methods=["GET", "POST"]),
            *(Route(path, agent_pages) for path in ("/agents", "/knowledge", "/simulation")),
            Route("/api/agents/overview", agents_overview_route),
            Route("/api/kb/search", kb_search),
            Route("/api/simulation", simulation, methods=["POST"]),
            *auth.routes(),
            WebSocketRoute("/ws", ws_endpoint),
            Mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static"),
        ],
        lifespan=lifespan,
        exception_handlers={404: page_not_found},
    )

    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'self'; "
            f"connect-src 'self' {settings.site_origin.replace('http', 'ws', 1)}"
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        if auth.secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.site_origin).hostname])
    return app


def datetime_string(visit):
    return visit["date"] + "T" + visit["start_time"]


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the CareBridge website")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo-doctor-signup", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="Accepted for compatibility")
    args = parser.parse_args()
    settings = get_settings()
    if args.port != 8000 and settings.site_origin == "http://127.0.0.1:8000":
        settings = replace(settings, site_origin=f"http://127.0.0.1:{args.port}")
    if args.demo_doctor_signup:
        settings = replace(settings, demo_doctor_signup=True)
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
