"""Keychain wrapper — reuses the same 'portfolio-monitor' service as the US monitor.

Gmail credentials are already stored there from the Fidelity monitor bootstrap.
No separate bootstrap needed.
"""

from __future__ import annotations

import keyring

SERVICE_NAME = "portfolio-monitor"   # shared with Fidelity monitor


class SecretMissing(RuntimeError):
    pass


def get_secret(key: str, *, required: bool = True) -> str | None:
    value = keyring.get_password(SERVICE_NAME, key)
    if value is None and required:
        raise SecretMissing(
            f"Missing keychain entry: {SERVICE_NAME}/{key}. "
            "Run the portfolio-monitor bootstrap first."
        )
    return value
