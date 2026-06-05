"""Google credentials helper.

Centralises how the agent obtains GCP credentials. In GKE the agent runs with
Workload Identity (ambient ADC). It can optionally impersonate a dedicated
service account so all GCP access is least-privilege and auditable.
"""

from __future__ import annotations

import functools

import google.auth
from google.auth import impersonated_credentials

from agent.config import SETTINGS

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


@functools.lru_cache(maxsize=1)
def get_credentials():
    """Return ADC, optionally wrapped in impersonation of the agent SA."""
    source_creds, _ = google.auth.default(scopes=_SCOPES)
    target = SETTINGS.impersonate_sa.strip()
    if not target:
        return source_creds
    # If we are already the target SA (e.g. WI maps directly), skip impersonation.
    if getattr(source_creds, "service_account_email", None) == target:
        return source_creds
    try:
        return impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=target,
            target_scopes=_SCOPES,
            lifetime=3600,
        )
    except Exception:
        # Impersonation not configured / not permitted -> fall back to ADC.
        return source_creds
