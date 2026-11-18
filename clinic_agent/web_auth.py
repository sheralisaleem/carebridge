"""Email and password account pages for the patient and doctor portals."""
from __future__ import annotations

import asyncio
import secrets
from urllib.parse import urlsplit

from starlette.responses import RedirectResponse
from starlette.routing import Route

from .auth import AuthError, SESSION_LIFETIME, send_reset_email
from .doctor_schedule import DEPARTMENTS

AUTH_PAGES = {
    "/sign-in": ("Sign in", "signin"),
    "/sign-up": ("Create an account", "signup"),
    "/doctor/sign-up": ("Create a doctor account", "doctor_signup"),
    "/forgot-password": ("Forgot password?", "forgot"),
    "/reset-password": ("Reset password", "reset"),
}


def return_path(value):
    value = str(value or "")
    if len(value) <= 512 and value.startswith("/book") and "\\" not in value:
        try:
            url = urlsplit(value)
        except ValueError:
            return ""
        if url.path == "/book" and not (url.scheme or url.netloc):
            return url.path + (f"?{url.query}" if url.query else "")
    return ""


class WebAuth:
    def __init__(self, settings, store, render, mail=None):
        self.settings, self.accounts, self.render = settings, store, render
        self.secure = settings.site_origin.startswith("https://")
        self.cookie = "__Host-carebridge" if self.secure else "carebridge"
        self.csrf_cookie = "__Host-cb_csrf" if self.secure else "cb_csrf"
        origin = urlsplit(settings.site_origin)
        if origin.scheme not in {"https", "http"} or not origin.hostname or origin.username or origin.path or origin.query or origin.fragment:
            raise ValueError("SITE_ORIGIN must be an origin such as http://127.0.0.1:8000.")
        local = origin.hostname in {"localhost", "127.0.0.1", "::1"}
        self.doctor_signup = local and settings.demo_doctor_signup
        self.demo_mail = self.doctor_signup and not settings.smtp_host and mail is None
        self.mail_ready = bool(settings.smtp_host or mail or self.demo_mail)
        self.mail = mail or (lambda address, token: send_reset_email(settings, address, token, self.demo_mail))
        if not self.secure and not local:
            raise ValueError("SITE_ORIGIN must use HTTPS outside localhost.")

    def user(self, request):
        return self.accounts.user_for_session(request.cookies.get(self.cookie))

    def set_cookie(self, response, key, value, max_age):
        response.set_cookie(key, value, max_age=max_age, httponly=True, secure=self.secure, samesite="lax", path="/")

    def csrf(self, request):
        value = request.cookies.get(self.csrf_cookie, "")
        return value if len(value) == 43 and value.isascii() else secrets.token_urlsafe(32)

    def validate_post(self, request, token):
        cookie = request.cookies.get(self.csrf_cookie)
        origin = request.headers.get("origin")
        if not cookie or not isinstance(token, str) or not token.isascii() or not secrets.compare_digest(cookie, token) or (
            origin and origin != self.settings.site_origin
        ):
            raise AuthError("This form expired. Reload the page and try again.")

    def landing(self, user, next_url=""):
        return "/doctor/appointments" if user["role"] == "doctor" else return_path(next_url) or "/dashboard"

    def sign_in(self, request, user, next_url=""):
        response = RedirectResponse(self.landing(user, next_url), 303)
        token = self.accounts.create_session(user["id"], request.cookies.get(self.cookie))
        self.set_cookie(response, self.cookie, token, SESSION_LIFETIME)
        self.set_cookie(response, self.csrf_cookie, secrets.token_urlsafe(32), SESSION_LIFETIME)
        return response

    def show(self, request, title, mode, *, error="", notice="", status=200, **fields):
        return self.render(
            request, "auth.html", title, status=status, mode=mode, error=error, notice=notice,
            departments=DEPARTMENTS, doctor_signup=self.doctor_signup,
            demo_mail=self.demo_mail, **fields,
        )

    async def page(self, request):
        path = request.url.path
        title, mode = AUTH_PAGES[path]
        if mode == "doctor_signup" and not self.doctor_signup:
            return self.render(request, "message.html", "Page unavailable", status=404,
                               message="Doctor registration is unavailable.")
        next_url = return_path(request.query_params.get("next"))
        common = {"next_url": next_url, "token": request.query_params.get("token", ""),
                  "email": "", "name": "", "department": ""}
        if request.method == "GET":
            return self.show(request, title, mode, **common)

        try:
            form = await request.form(max_fields=12, max_part_size=4096)
            self.validate_post(request, form.get("csrf"))
            address = str(form.get("email", ""))[:254]
            name = str(form.get("name", ""))[:100]
            password = str(form.get("password", ""))
            common.update(email=address, name=name, department=str(form.get("department", "")),
                          next_url=return_path(form.get("next")), token=str(form.get("token", "")))
            client = request.client.host if request.client else "local"
            if not self.accounts.rate_limit(f"{mode}:{client}", 24) or (
                address and not self.accounts.rate_limit(f"{mode}:address:{address.strip().casefold()}", 8)
            ):
                return self.show(request, title, mode, status=429, error="Too many attempts. Try again in a few minutes.", **common)
            if mode == "signin":
                user = await asyncio.to_thread(self.accounts.check_password, address, password)
                return self.sign_in(request, user, common["next_url"])
            if mode in ("signup", "doctor_signup"):
                role = "doctor" if mode == "doctor_signup" else "patient"
                user = await asyncio.to_thread(self.accounts.register, name, address, password, role,
                                               common["department"])
                return self.sign_in(request, user, common["next_url"])
            if mode == "forgot":
                if not self.mail_ready:
                    raise AuthError("Password-reset email is unavailable. Try again later.")
                email, token = self.accounts.request_reset(address)
                if token:
                    await asyncio.to_thread(self.mail, email, token)
                return self.show(request, title, mode, notice="If an account matches, an email will arrive shortly.",
                                 **common)
            if not await asyncio.to_thread(self.accounts.reset, common["token"], password):
                raise AuthError("This reset link has expired. Request a new password reset.")
            return self.show(request, "Password updated", "updated", **common)
        except AuthError as exc:
            return self.show(request, title, mode, error=str(exc), status=400, **common)

    async def logout(self, request):
        form = await request.form(max_fields=3, max_part_size=1024)
        try:
            self.validate_post(request, form.get("csrf"))
        except AuthError as exc:
            return self.render(request, "message.html", "Sign out", status=400, message=str(exc))
        self.accounts.revoke_session(request.cookies.get(self.cookie))
        response = RedirectResponse("/", 303)
        for cookie in (self.cookie, self.csrf_cookie):
            response.delete_cookie(cookie, secure=self.secure, httponly=True, path="/")
        response.headers["Clear-Site-Data"] = '"storage"'
        return response

    def routes(self):
        return [
            *(Route(path, self.page, methods=["GET", "POST"]) for path in AUTH_PAGES),
            Route("/sign-out", self.logout, methods=["POST"]),
        ]
