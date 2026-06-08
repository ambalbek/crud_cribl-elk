"""Shared constants — timeouts, exit codes, URLs."""

from __future__ import annotations

# Cribl Cloud
CRIBL_CLOUD_LOGIN_URL = "https://login.cribl.cloud/oauth/token"
CRIBL_CLOUD_AUDIENCE = "https://api.cribl.cloud"

# HTTP timeouts (seconds)
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
CAPTURE_READ_TIMEOUT_PAD = 30

# Process exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2
EXIT_INTERRUPTED = 130
