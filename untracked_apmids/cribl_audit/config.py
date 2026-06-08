"""Configuration loading — .env files and JSON config files."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from typing import Any

log = logging.getLogger("cribl_audit")

# Maps capture-section config keys to argparse dest names (where they differ)
_CAPTURE_KEY_MAP = {
    "groups": "group",
    "appid_field": "appid_field",
    "max_events": "max_events",
}


def load_env_file(path: str) -> None:
    """Load KEY=VALUE pairs from *path* into ``os.environ``.

    Supports blank lines, ``#`` comments, ``export`` prefix, and quoted
    values.  Does **not** override variables already set.
    """
    try:
        if hasattr(os, "stat"):
            try:
                mode = os.stat(path).st_mode
                if mode & stat.S_IROTH:
                    log.warning(
                        "env file %s is world-readable (mode %o). "
                        "Run: chmod 600 %s",
                        path, stat.S_IMODE(mode), path,
                    )
            except OSError:
                pass

        with open(path, encoding="utf-8") as fh:
            for lineno, raw_line in enumerate(fh, 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    log.warning("%s:%d: skipping line without '='", path, lineno)
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
                    log.debug("Loaded %s from %s", key, path)
    except FileNotFoundError:
        sys.exit(f"ERROR: env file not found: {path}")
    except PermissionError:
        sys.exit(f"ERROR: cannot read env file (permission denied): {path}")


def load_config(path: str) -> dict[str, Any]:
    """Load a JSON config file and return a flat dict of argparse-compatible defaults.

    Credentials from the ``auth`` section are pushed into ``os.environ``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"ERROR: config file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: invalid JSON in {path}: {exc}")

    if not isinstance(raw, dict):
        sys.exit(f"ERROR: config file must be a JSON object, got {type(raw).__name__}")

    defaults: dict[str, Any] = {}

    # --- auth section -> env vars ---
    auth = raw.get("auth", {})
    env_mapping = {
        "cribl_url": "CRIBL_URL",
        "client_id": "CRIBL_CLIENT_ID",
        "client_secret": "CRIBL_CLIENT_SECRET",
        "username": "CRIBL_USERNAME",
        "password": "CRIBL_PASSWORD",
        "token": "CRIBL_TOKEN",
    }
    for key, env_name in env_mapping.items():
        val = auth.get(key, "")
        if val and env_name not in os.environ:
            os.environ[env_name] = str(val)
            log.debug("Loaded %s from config auth section", env_name)

    # --- capture section ---
    capture = raw.get("capture", {})
    for key in ("groups", "filter", "seconds", "max_events", "level",
                "rounds", "interval", "appid_field"):
        if key in capture:
            dest = _CAPTURE_KEY_MAP.get(key, key)
            defaults[dest] = capture[key]

    # --- matching section ---
    matching = raw.get("matching", {})
    if "mode" in matching:
        defaults["match_mode"] = matching["mode"]
    if "default_output" in matching:
        defaults["default_output"] = matching["default_output"]
        if "CRIBL_DEFAULT_OUTPUT_ID" not in os.environ:
            os.environ["CRIBL_DEFAULT_OUTPUT_ID"] = str(matching["default_output"])

    # --- output section ---
    output = raw.get("output", {})
    for key in ("format", "append"):
        if key in output:
            defaults[key] = output[key]
    if "diff_csv" in output:
        defaults["diff_csv"] = output["diff_csv"]
    if "lookup" in output:
        defaults["lookup"] = output["lookup"]

    # --- elasticsearch section ---
    es_cfg = raw.get("elasticsearch", {})
    if "url" in es_cfg:
        defaults["es_url"] = es_cfg["url"]
    if "index" in es_cfg:
        defaults["es_index"] = es_cfg["index"]
    es_env_map = {
        "api_key": "ES_API_KEY",
        "username": "ES_USERNAME",
        "password": "ES_PASSWORD",
    }
    for key, env_name in es_env_map.items():
        val = es_cfg.get(key, "")
        if val and env_name not in os.environ:
            os.environ[env_name] = str(val)

    # --- logging section ---
    log_cfg = raw.get("logging", {})
    if "log_file" in log_cfg:
        defaults["log_file"] = log_cfg["log_file"]
    if "verbose" in log_cfg:
        defaults["verbose"] = log_cfg["verbose"]

    # --- connection section ---
    conn = raw.get("connection", {})
    if "verify_ssl" in conn:
        defaults["no_verify_ssl"] = not conn["verify_ssl"]
    if "env_file" in conn:
        defaults["env_file"] = conn["env_file"]

    return defaults
