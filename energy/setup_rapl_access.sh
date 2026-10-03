#!/usr/bin/env bash
# Grants passwordless read access to RAPL energy counters for the current user.
# The project plan (Part I, step 2) requires hardware energy counters; on Linux
# these are root-owned 0400 files under /sys/class/powercap.
#
# Usage:
#   sudo ./energy/setup_rapl_access.sh          # grant access (survives reboot)
#   sudo ./energy/setup_rapl_access.sh revert   # remove the granted access
#
# Strategy: install a NOPASSWD sudo rule allowing only `cat` on RAPL energy
# files. This is the least-privilege option that survives /sys re-mounts and
# reboots. (chmod on /sys files does not persist.)

set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/rapl-energy-reader"
RAPL_GLOB="/sys/class/powercap/intel-rapl:*/energy_uj"

if [[ "${1:-}" == "revert" ]]; then
  rm -f "$SUDOERS_FILE"
  echo "Removed $SUDOERS_FILE — RAPL access revoked."
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

# visudo -c validates syntax before we install; -f checks a candidate file.
cat > "/tmp/rapl-sudoers-check" <<EOF
$(whoami 2>/dev/null || true)
EOF

# The rule allows the invoking user (SUDO_USER, since we run under sudo) to
# cat exactly the RAPL energy files, nothing else.
REAL_USER="${SUDO_USER:?SUDO_USER not set - run this via sudo from your account}"
cat > "$SUDOERS_FILE" <<EOF
# Managed by energy-aware-llvm/energy/setup_rapl_access.sh
# Allows reading RAPL package energy counters only.
${REAL_USER} ALL=(root) NOPASSWD: /usr/bin/cat ${RAPL_GLOB}
EOF

chmod 440 "$SUDOERS_FILE"
visudo -c -f "$SUDOERS_FILE" >/dev/null

# Verify it actually works end-to-end.
if sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj >/dev/null 2>&1; then
  echo "OK: RAPL counters are now readable without a password."
  echo "Rule installed at $SUDOERS_FILE (sudo $0 revert to undo)."
else
  echo "WARNING: rule installed but verification read failed; check sudo logs." >&2
  exit 1
fi
