from flask import Flask

from app.routes.health import health_bp
from app.routes.intake import intake_bp
from app.routes.engagement import engagement_bp
from app.routes.solutioning import solutioning_bp
from app.routes.delivery import delivery_bp
from app.routes.validation import validation_bp
from app.routes.requests import requests_bp


def register_blueprints(flask_app: Flask) -> None:
    """Register all route blueprints on the Flask application."""
    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(intake_bp)
    flask_app.register_blueprint(engagement_bp)
    flask_app.register_blueprint(solutioning_bp)
    flask_app.register_blueprint(delivery_bp)
    flask_app.register_blueprint(validation_bp)
    flask_app.register_blueprint(requests_bp)
