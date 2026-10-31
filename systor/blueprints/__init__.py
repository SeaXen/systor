"""Flask Blueprints registration for Systor."""
from __future__ import annotations
from flask import Flask

from .auth import auth_bp
from .dashboard import dashboard_bp
from .apps import apps_bp
from .network import network_bp
from .speed import speed_bp
from .storage import storage_bp
from .settings import settings_bp


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(apps_bp)
    app.register_blueprint(network_bp)
    app.register_blueprint(speed_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(settings_bp)
