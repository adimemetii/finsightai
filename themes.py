"""
Theme tokens and switcher for FinSight AI.

Two themes ship today: dark (default) and light. The token set is exposed
both to Jinja (so base.html can set data-bs-theme + body class) and to
JS (so a switcher can persist the choice without a server roundtrip).
"""

from __future__ import annotations

from flask import jsonify, redirect, request, session, url_for


THEMES = ("dark", "light")
DEFAULT_THEME = "dark"

THEME_NAMES = {
    "dark": {"sq": "E errët", "en": "Dark", "de": "Dunkel", "zh": "深色"},
    "light": {"sq": "E ndritshme", "en": "Light", "de": "Hell", "zh": "浅色"},
}


def _is_valid_theme(theme: str) -> bool:
    return theme in THEMES


def get_theme() -> str:
    """Resolve the active theme for the current request."""
    requested = (request.args.get("theme") or "").lower()
    if _is_valid_theme(requested):
        return requested
    sess = session.get("theme")
    if _is_valid_theme(sess):
        return sess
    return DEFAULT_THEME


def set_theme(theme: str) -> bool:
    if not _is_valid_theme(theme):
        return False
    session["theme"] = theme
    return True


def init_app(app) -> None:
    """Register theme hooks on a Flask app."""

    @app.before_request
    def _themes_before_request():
        requested = (request.args.get("theme") or "").lower()
        if _is_valid_theme(requested):
            session["theme"] = requested

    @app.context_processor
    def _themes_context():
        active = get_theme()
        return {
            "theme": active,
            "themes": list(THEMES),
            "theme_names": THEME_NAMES,
        }

    @app.route("/set-theme/<theme>")
    def _set_theme(theme: str):
        if not _is_valid_theme(theme):
            return redirect(request.referrer or url_for("index"))
        set_theme(theme)
        target = request.referrer or url_for("index")
        if "/set-theme/" in target or "/set-language/" in target:
            target = url_for("index")
        return redirect(target)

    @app.route("/api/theme")
    def _api_theme():
        return jsonify({"theme": get_theme(), "available": list(THEMES)})
