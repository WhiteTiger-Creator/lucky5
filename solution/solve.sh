#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Contain the host per /app/docs/containment_runbook.md ---

# Revoke the rollout automation's SSH persistence, preserving any other keys.
if [ -f /root/.ssh/authorized_keys ]; then
  grep -v 'cert-rotation-automation' /root/.ssh/authorized_keys > /root/.ssh/authorized_keys.tmp || true
  mv /root/.ssh/authorized_keys.tmp /root/.ssh/authorized_keys
  chmod 600 /root/.ssh/authorized_keys
fi

# Remove the passwordless sudoers escalation entirely.
rm -f /etc/sudoers.d/cert-rotation

# Lock down the exposed rotation signing key (keep it, restrict to root 0600).
chown root:root /app/secrets/rotation_signing.key
chmod 0600 /app/secrets/rotation_signing.key

# --- Restore the reconciler and produce responder outputs ---

cp "${SCRIPT_DIR}/export_report_fixed.py" /app/workflow/export_report.py
python3 /app/workflow/export_report.py --output-dir /app/output
