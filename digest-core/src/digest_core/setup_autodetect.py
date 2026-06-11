"""Best-effort local environment autodetection for the setup wizard.

Sources — all local, read-only, every probe degrades to None/empty:
  - machine login:  getpass/pwd (whoami)
  - real name:      pwd gecos, then `dscl . -read /Users/<login> RealName` (macOS)
  - domain hints:   `dsconfigad -show` (AD bind), `scutil --dns` search domains (macOS)
  - emails:         ASCII scan of ~/Library/Keychains/*.keychain-db. Only the
                    file's *unencrypted* item labels/account names are visible
                    this way; keychain secrets are encrypted and unreadable.
                    Candidates stay in memory — never logged, never persisted.
  - EWS host:       candidates shaped <login>@<host> where the host belongs to
                    the UPN/hinted domain (e.g. ruapgr2@owa.megacorp.ru)
  - reachability:   DNS resolution with a short timeout. Corp hosts resolve only
                    inside the perimeter, so failure lowers confidence (the
                    wizard asks instead of skipping) — it never blocks.

The corp email (UPN) is *discovered*, not synthesized from the real name:
local parts are not always name.surname. The real name only ranks candidates.
Known limitation: a Cyrillic RealName never matches a Latin email local part —
the candidate then surfaces as a prompt default instead of an auto-fill.
"""

from __future__ import annotations

import concurrent.futures
import getpass
import os
import re
import socket
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

CONF_NONE = "none"
CONF_MEDIUM = "medium"
CONF_HIGH = "high"

# Bounded email shape on raw bytes (local 1..64, domain labels + alpha TLD).
_EMAIL_RE = re.compile(
    rb"[A-Za-z0-9][A-Za-z0-9._%+\-]{0,63}@[A-Za-z0-9][A-Za-z0-9.\-]{1,251}\.[A-Za-z]{2,16}"
)

# Public mail providers — heavily penalized as *corp UPN* candidates.
_PUBLIC_PROVIDERS = {
    "gmail.com",
    "googlemail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "apple.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "proton.me",
    "protonmail.com",
    "yandex.ru",
    "ya.ru",
    "mail.ru",
    "bk.ru",
    "inbox.ru",
    "list.ru",
    "rambler.ru",
}

# DNS search-domain suffixes that are not corp domains.
_JUNK_DOMAIN_SUFFIXES = (".arpa", ".local", ".localdomain")

_SCAN_CHUNK = 8 * 1024 * 1024
_SCAN_OVERLAP = 512  # an email never exceeds this; survives chunk boundaries

Runner = Callable[[list[str]], Optional[str]]


def _default_runner(cmd: list[str]) -> Optional[str]:
    """Run a probe command; stdout on success, None on any failure."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


@dataclass
class EmailCandidate:
    address: str
    score: int = 0
    count: int = 1
    reasons: list[str] = field(default_factory=list)

    @property
    def local(self) -> str:
        return self.address.partition("@")[0]

    @property
    def domain(self) -> str:
        return self.address.partition("@")[2].lower()


@dataclass
class DetectedEnv:
    login: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    domain_hints: list[str] = field(default_factory=list)
    emails: list[EmailCandidate] = field(default_factory=list)
    best_upn: Optional[str] = None
    upn_confidence: str = CONF_NONE
    ews_host: Optional[str] = None
    ews_endpoint_verified: bool = False
    notes: list[str] = field(default_factory=list)

    def has_findings(self) -> bool:
        return bool(self.best_upn or self.ews_host or self.first_name or self.domain_hints)


# ---------------------------------------------------------------------------
# Individual probes (pure where possible)
# ---------------------------------------------------------------------------


def _machine_login() -> Optional[str]:
    try:
        login = getpass.getuser().strip()
    except Exception:
        return None
    return login or None


def _parse_real_name(raw: str) -> tuple[Optional[str], Optional[str]]:
    """Split a directory RealName into (first, last).

    Corp AD convention writes the surname in caps ("POGORELOV Ruslan") —
    the all-caps token is the surname regardless of position. Without that
    signal, assume "First Last" order.
    """
    tokens = [t for t in raw.replace(",", " ").split() if t]
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return tokens[0], None
    caps = [t for t in tokens if len(t) > 1 and t.isupper()]
    others = [t for t in tokens if t not in caps]
    if len(caps) == 1 and others:
        return others[0], caps[0]
    return tokens[0], tokens[-1]


def _real_name_raw(login: Optional[str], runner: Runner) -> Optional[str]:
    try:
        import pwd

        entry = pwd.getpwnam(login) if login else pwd.getpwuid(os.getuid())
        gecos = (entry.pw_gecos or "").split(",")[0].strip()
        if gecos:
            return gecos
    except Exception:
        pass

    if sys.platform == "darwin" and login:
        out = runner(["dscl", ".", "-read", f"/Users/{login}", "RealName"])
        if out:
            # "RealName:\n POGORELOV Ruslan"  or  "RealName: POGORELOV Ruslan"
            value = out.partition("RealName:")[2].strip()
            if value:
                return value.splitlines()[0].strip() or None
    return None


def _ad_domain(runner: Runner) -> Optional[str]:
    if sys.platform != "darwin":
        return None
    out = runner(["dsconfigad", "-show"])
    if not out:
        return None
    for line in out.splitlines():
        if "Active Directory Domain" in line:
            domain = line.partition("=")[2].strip().lower()
            if domain:
                return domain
    return None


def _dns_search_domains(runner: Runner) -> list[str]:
    if sys.platform != "darwin":
        return []
    out = runner(["scutil", "--dns"])
    if not out:
        return []
    domains: list[str] = []
    for match in re.finditer(r"search domain\[\d+\]\s*:\s*(\S+)", out):
        domain = match.group(1).strip().lower().rstrip(".")
        if not domain or "." not in domain:
            continue
        if domain.endswith(_JUNK_DOMAIN_SUFFIXES):
            continue
        if domain not in domains:
            domains.append(domain)
    return domains


def _extract_emails(data: bytes) -> list[str]:
    out: list[str] = []
    for match in _EMAIL_RE.finditer(data):
        try:
            addr = match.group(0).decode("ascii")
        except UnicodeDecodeError:
            continue
        local, _, domain = addr.partition("@")
        if ".." in addr or local.endswith(".") or domain.startswith((".", "-")):
            continue
        out.append(addr)
    return out


def _scan_keychain_emails(keychain_dir: Path) -> Counter:
    """Count email-shaped ASCII strings across *.keychain-db files.

    Chunked read with overlap so candidates spanning a chunk boundary are
    still seen; unreadable files are skipped silently.
    """
    counts: Counter = Counter()
    if not keychain_dir.is_dir():
        return counts
    for db in sorted(keychain_dir.glob("*.keychain-db")):
        try:
            with open(db, "rb") as f:
                tail = b""
                while True:
                    chunk = f.read(_SCAN_CHUNK)
                    if not chunk:
                        break
                    counts.update(_extract_emails(tail + chunk))
                    tail = chunk[-_SCAN_OVERLAP:]
        except OSError:
            continue
    return counts


def _group_by_lower(counts: Counter) -> dict[str, tuple[str, int]]:
    """lower-address -> (most frequent original casing, total count)."""
    grouped: dict[str, dict[str, int]] = {}
    for addr, n in counts.items():
        grouped.setdefault(addr.lower(), {})
        grouped[addr.lower()][addr] = grouped[addr.lower()].get(addr, 0) + n
    out: dict[str, tuple[str, int]] = {}
    for low, variants in grouped.items():
        best = max(variants.items(), key=lambda kv: kv[1])[0]
        out[low] = (best, sum(variants.values()))
    return out


def _rank_emails(
    counts: Counter,
    login: Optional[str],
    name_tokens: list[str],
    domain_hints: list[str],
) -> list[EmailCandidate]:
    login_low = (login or "").lower()
    tokens = [t.lower() for t in name_tokens if t and len(t) > 1]
    hints = [h.lower() for h in domain_hints]

    candidates: list[EmailCandidate] = []
    for low, (display, total) in _group_by_lower(counts).items():
        cand = EmailCandidate(address=display, count=total)
        local_low, _, domain = low.partition("@")
        local_parts = [p for p in re.split(r"[._\-+]", local_low) if p]

        if tokens:
            matched = [t for t in tokens if t in local_parts]
            if len(matched) == len(tokens):
                cand.score += 50
                cand.reasons.append("имя и фамилия в адресе")
            elif matched:
                cand.score += 20
                cand.reasons.append("часть имени в адресе")

        if any(domain == h or domain.endswith("." + h) for h in hints):
            cand.score += 25
            cand.reasons.append("домен совпадает с сетью")

        if domain in _PUBLIC_PROVIDERS:
            cand.score -= 40

        if login_low and local_low == login_low:
            # login@host artifacts are service identities (OWA/EWS), not the UPN
            cand.score -= 15
            cand.reasons.append("служебный адрес (логин@хост)")

        cand.score += min(total, 10)
        candidates.append(cand)

    candidates.sort(key=lambda c: (-c.score, c.address))
    return candidates


def _extract_ews_host(
    ranked: list[EmailCandidate],
    login: Optional[str],
    upn: Optional[str],
    domain_hints: list[str],
) -> Optional[str]:
    """Host from <login|upn-local>@<host> artifacts inside the corp domain."""
    locals_ok = {x for x in ((login or "").lower(), (upn or "").partition("@")[0].lower()) if x}
    upn_domain = (upn or "").partition("@")[2].lower()
    suffixes = [d for d in ([upn_domain] if upn_domain else []) + list(domain_hints) if d]
    if not locals_ok or not suffixes:
        return None

    def in_corp(host: str) -> bool:
        return any(host == s or host.endswith("." + s) for s in suffixes)

    hosts = [
        (c.domain, c.count)
        for c in ranked
        if c.local.lower() in locals_ok and "." in c.domain and in_corp(c.domain)
    ]
    if not hosts:
        return None

    def rank(item: tuple[str, int]) -> tuple[int, int]:
        host, count = item
        prefix_rank = next(
            (i for i, p in enumerate(("owa.", "mail.", "exchange.", "ews.")) if host.startswith(p)),
            9,
        )
        return (prefix_rank, -count)

    best = min(hosts, key=rank)[0]
    # A bare-domain artifact (upn-shaped) is not a service host.
    return best if best != upn_domain else None


def _dns_resolves(host: str, timeout: float = 1.5) -> bool:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(socket.getaddrinfo, host, 443)
        try:
            future.result(timeout=timeout)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def detect_environment(
    home: Optional[Path] = None,
    runner: Runner = _default_runner,
    dns_check: bool = True,
) -> DetectedEnv:
    """Gather everything detectable; never raises, never prompts."""
    det = DetectedEnv()
    home = home or Path.home()

    det.login = _machine_login()
    raw_name = _real_name_raw(det.login, runner)
    if raw_name:
        det.first_name, det.last_name = _parse_real_name(raw_name)

    ad = _ad_domain(runner)
    det.domain_hints = ([ad] if ad else []) + [d for d in _dns_search_domains(runner) if d != ad]

    counts = _scan_keychain_emails(home / "Library" / "Keychains")
    name_tokens = [t for t in (det.first_name, det.last_name) if t]
    det.emails = _rank_emails(counts, det.login, name_tokens, det.domain_hints)

    if det.emails and det.emails[0].score >= 25:
        best = det.emails[0]
        det.best_upn = best.address
        full_name_match = "имя и фамилия в адресе" in best.reasons
        domain_match = "домен совпадает с сетью" in best.reasons
        det.upn_confidence = CONF_HIGH if (full_name_match and domain_match) else CONF_MEDIUM

    det.ews_host = _extract_ews_host(det.emails, det.login, det.best_upn, det.domain_hints)
    if det.ews_host:
        # Cross-artifact corroboration: a login@<host> inside the UPN's domain
        # confirms the UPN pick even when network hints were unavailable.
        upn_domain = (det.best_upn or "").partition("@")[2].lower()
        if (
            det.best_upn
            and det.upn_confidence == CONF_MEDIUM
            and upn_domain
            and det.ews_host.endswith("." + upn_domain)
            and "имя и фамилия в адресе" in det.emails[0].reasons
        ):
            det.upn_confidence = CONF_HIGH
        if dns_check:
            det.ews_endpoint_verified = _dns_resolves(det.ews_host)

    _build_notes(det)
    return det


def _build_notes(det: DetectedEnv) -> None:
    if det.login:
        det.notes.append(f"Логин: {det.login}")
    if det.first_name or det.last_name:
        full = " ".join(t for t in (det.first_name, det.last_name) if t)
        det.notes.append(f"Имя: {full} (из учётной записи)")
    if det.domain_hints:
        det.notes.append("Домены сети: " + ", ".join(det.domain_hints[:3]))
    if det.best_upn:
        reasons = ", ".join(det.emails[0].reasons) if det.emails[0].reasons else "по частоте"
        det.notes.append(f"Email (Keychain): {det.best_upn} — {reasons}")
    elif det.emails:
        det.notes.append("Email в Keychain уверенно не определён — спрошу")
    if det.ews_host:
        dns = "DNS ✓" if det.ews_endpoint_verified else "DNS не отвечает (вне корп-сети?)"
        det.notes.append(f"EWS host: {det.ews_host} — {dns}")
