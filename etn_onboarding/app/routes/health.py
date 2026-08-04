import logging

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__)


@health_bp.route("/", methods=["GET"])
def index():
    """Root endpoint — service info."""
    return jsonify({
        "service": "ETN Onboarding",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "ready": "/ready",
            "intake": "/api/intake",
            "engagement": "/api/engagement",
            "solutioning": "/api/solutioning",
            "delivery": "/api/delivery",
            "validation": "/api/validation",
            "requests": "/api/requests",
        },
    }), 200


@health_bp.route("/health", methods=["GET"])
def liveness():
    """Liveness probe -- always returns 200 if the process is running."""
    return jsonify({"status": "ok"}), 200


@health_bp.route("/ready", methods=["GET"])
def readiness():
    """Readiness probe -- verifies the database connection is usable."""
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ready"}), 200
    except Exception:
        logger.exception("Readiness check failed: database unreachable")
        return jsonify({"status": "unavailable", "error": "database connection failed"}), 503
