"""Compatibility entry point for the FinSight AI web application.

The old version of this file was an interactive, single-dataset CLI that
duplicated the Flask application's cleaning, ML, and database logic.  Keeping
this wrapper makes an existing ``python main.py`` command safe while the
production entry point remains ``app:app`` (or ``python app.py`` locally).
"""

from __future__ import annotations

import os

from app import app


def main() -> None:
    app.run(
        host=os.environ.get("FLASK_RUN_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", os.environ.get("FLASK_RUN_PORT", "5000"))),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() in {"1", "true", "yes", "on"},
    )


if __name__ == "__main__":
    main()
