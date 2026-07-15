# Post-Rollout Containment Runbook

Required hardened state for endpoint hosts touched by the failed TLS certificate-rotation rollout. The rotation automation provisioned itself with persistence and escalation paths that must be removed during containment; the reconciler restore does not begin until the host matches this runbook.

## SSH persistence

The rotation automation appended its deploy key to root's `authorized_keys`, identified by the key comment `cert-rotation-automation`. Revoke it: no entry carrying that comment may remain in `/root/.ssh/authorized_keys`. Other entries in the file, if any, are legitimate and stay.

## Privilege escalation

The rollout installed a temporary sudoers drop-in at `/etc/sudoers.d/cert-rotation` granting `svc-certd` passwordless root. Remove the file entirely — do not comment it out or narrow it.

## Key exposure

The rotation signing key at `/app/secrets/rotation_signing.key` was written world-readable. The key itself remains valid and the rotation service still needs it in place: keep the file, owned `root:root`, mode `0600`.
