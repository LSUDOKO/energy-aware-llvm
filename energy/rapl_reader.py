"""RAPL (Running Average Power Limit) energy counter reader.

Reads package energy counters from /sys/class/powercap (intel-rapl driver,
which also exposes AMD package energy on Zen chips). The counter nodes are
root-owned with mode 0400 by default, so this module supports three access
strategies, tried in order:

  1. direct   - the counter file is readable (already chmod'd or ACL'd)
  2. sudo     - a configured sudo rule permits NOPASSWD read (see
                energy/setup_rapl_access.sh)
  3. fallback - energy sampling is unavailable; callers must treat every
                reading as unavailable and fall back to time-only mode.

The counter is a monotonically increasing microjoule register that wraps
around at max_energy_range_uj. We handle wrap explicitly per the project
plan (Part I, step 5).

Typical use:
    reader = RAPLReader.create()          # picks best available strategy
    if reader.available():
        e = reader.read()                 # EnergyReading(package_uj=..., ...)
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

RAPL_BASE = Path("/sys/class/powercap")


@dataclass(frozen=True)
class EnergyReading:
    """A single raw counter snapshot. Monotonic within a domain until wrap."""

    package_uj: int
    timestamp: float          # time.monotonic() at read moment
    domain: str               # e.g. "package-0"
    max_range_uj: int | None  # wrap point, if the kernel exposes it


@dataclass(frozen=True)
class RAPLDomain:
    """One RAPL counter node (package or subdomain)."""

    name: str
    path: Path
    max_range_uj: int | None

    def _read_int(self) -> int:
        with open(self.path / "energy_uj", "rb") as f:
            return int(f.read().strip())

    def read_raw(self) -> EnergyReading:
        return EnergyReading(
            package_uj=self._read_int(),
            timestamp=time.monotonic(),
            domain=self.name,
            max_range_uj=self.max_range_uj,
        )


class RAPLReader:
    """Reads one or more RAPL domains using the best available strategy."""

    def __init__(self, domains: list[RAPLDomain], strategy: str, sudo_prefix: list[str] | None = None):
        self.domains = domains
        self.strategy = strategy  # "direct" | "sudo"
        self._sudo_prefix = sudo_prefix or []

    # ------------------------------------------------------------------ #
    # Construction / capability detection
    # ------------------------------------------------------------------ #

    @classmethod
    def create(cls, prefer_domain: str | None = None) -> "RAPLReader | UnavailableRAPL":
        """Detect RAPL domains and choose an access strategy.

        Returns an UnavailableRAPL (with .available() == False) when no
        readable strategy exists, so callers can degrade gracefully.
        """
        domains = cls._discover_domains()
        if not domains:
            return UnavailableRAPL(reason="no RAPL domains found under /sys/class/powercap")

        target = domains[0]
        if prefer_domain:
            for d in domains:
                if d.name == prefer_domain:
                    target = d
                    break

        if cls._readable_directly(target):
            return cls([target], strategy="direct")

        if cls._readable_via_sudo(target):
            return cls([target], strategy="sudo", sudo_prefix=["sudo", "-n"])

        return UnavailableRAPL(
            reason=(
                f"RAPL domain {target.name} exists but is not readable "
                f"(root-owned). Run energy/setup_rapl_access.sh to grant access, "
                f"or use --time-only mode."
            )
        )

    @staticmethod
    def _discover_domains() -> list[RAPLDomain]:
        domains: list[RAPLDomain] = []
        if not RAPL_BASE.exists():
            return domains
        # intel-rapl:K entries are top-level packages; sorted for determinism.
        for entry in sorted(RAPL_BASE.glob("intel-rapl:*")):
            if not entry.name.replace("intel-rapl:", "").isdigit():
                continue  # skip subdomain dirs like intel-rapl:0:0
            name_node = entry / "name"
            name = name_node.read_text().strip() if name_node.exists() else entry.name
            max_range = None
            max_node = entry / "max_energy_range_uj"
            if max_node.exists():
                try:
                    max_range = int(max_node.read_text().strip())
                except (ValueError, OSError):
                    pass
            domains.append(RAPLDomain(name=name, path=entry, max_range_uj=max_range))
        return domains

    @staticmethod
    def _readable_directly(domain: RAPLDomain) -> bool:
        energy = domain.path / "energy_uj"
        if not energy.exists():
            return False
        return os.access(energy, os.R_OK)

    @staticmethod
    def _readable_via_sudo(domain: RAPLDomain) -> bool:
        """Check for a NOPASSWD sudo rule without writing anything."""
        energy_path = str(domain.path / "energy_uj")
        try:
            probe = subprocess.run(
                ["sudo", "-n", "cat", energy_path],
                capture_output=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        return probe.returncode == 0 and probe.stdout.strip().isdigit()

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #

    def available(self) -> bool:
        return True

    def domain_name(self) -> str:
        return self.domains[0].name

    def read(self) -> EnergyReading:
        """Read the package energy counter.

        For the 'sudo' strategy we batch two syscalls (value + timestamp)
        into a single sudo invocation by reading via cat; the timestamp is
        taken immediately after the subprocess returns, which is accurate
        to within the process teardown time (~1-2 ms). The harness's batch
        technique keeps this overhead negligible relative to trial length.
        """
        domain = self.domains[0]
        if self.strategy == "direct":
            reading = domain.read_raw()
        else:
            energy_path = str(domain.path / "energy_uj")
            out = subprocess.run(
                self._sudo_prefix + ["cat", energy_path],
                capture_output=True, check=True, timeout=10,
            )
            reading = EnergyReading(
                package_uj=int(out.stdout.decode().strip()),
                timestamp=time.monotonic(),
                domain=domain.name,
                max_range_uj=domain.max_range_uj,
            )
        return reading

    def read_delta(self, before: EnergyReading, after: EnergyReading) -> int:
        """Raw microjoules between two readings, handling counter wrap.

        Per plan step 5: if the finite-width counter wrapped between the
        two reads, add the documented maximum range before subtracting.
        """
        delta = after.package_uj - before.package_uj
        if delta < 0 and after.max_range_uj:
            delta += after.max_range_uj
        if delta < 0:
            raise ValueError(
                f"negative energy delta ({delta} uJ) with no wrap range known; "
                f"counter semantics violated between {before.timestamp} and {after.timestamp}"
            )
        return delta

    def describe(self) -> str:
        max_range = self.domains[0].max_range_uj
        range_txt = f"{max_range / 1e6:.1f} J wrap range" if max_range else "unknown wrap range"
        return (
            f"RAPL domain '{self.domain_name()}' via {self.strategy} access, {range_txt}"
        )


class UnavailableRAPL:
    """Stand-in returned when no RAPL access strategy works."""

    def __init__(self, reason: str):
        self.reason = reason
        self.strategy = "unavailable"

    def available(self) -> bool:
        return False

    def describe(self) -> str:
        return f"RAPL unavailable: {self.reason}"


def amd_note() -> str:
    """Documented limitation: AMD Zen exposes package energy via the
    intel-rapl driver interface but typically has NO DRAM energy domain,
    unlike Intel. Reports should state the measurement covers CPU package
    only."""
    return (
        "On AMD platforms the intel-rapl driver exposes package energy only "
        "(no DRAM domain). All energy figures cover the CPU package unless "
        "noted otherwise."
    )
