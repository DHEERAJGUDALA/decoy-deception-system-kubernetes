"""
Attack Patterns — Detection logic for the traffic analyzer.

Classifies HTTP requests into attack categories using compiled regex,
rate tracking, and behavioral heuristics. Each detection method returns
a list of findings (may be empty), where each finding is a dict:

    {
        "attack_type": str,      # e.g. "sqli", "xss", "path_traversal"
        "confidence": float,     # 0.0 - 1.0
        "source_ip": str,
        "evidence": str,         # the matched pattern or trigger reason
        "timestamp": str,        # ISO 8601
        "raw_request_summary": dict
    }

All regex patterns are compiled at module load time for performance.
"""

import re
import time
from collections import defaultdict
from datetime import datetime, timezone


# ============================================================================
# Compiled regex patterns — loaded once at import time
# ============================================================================

# ---------------------------------------------------------------------------
# SQL Injection patterns
# ---------------------------------------------------------------------------
# Classic injection characters and keywords. Ordered roughly by severity.
_SQLI_PATTERNS = [
    # Tautologies and boolean-based blind
    (re.compile(r"\bOR\s+1\s*=\s*1\b", re.IGNORECASE), "OR 1=1 tautology", 0.95),
    (
        re.compile(r"\bOR\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?", re.IGNORECASE),
        "OR equality tautology",
        0.80,
    ),
    (re.compile(r"\bAND\s+1\s*=\s*1\b", re.IGNORECASE), "AND 1=1 tautology", 0.75),
    # UNION-based
    (re.compile(r"\bUNION\s+(ALL\s+)?SELECT\b", re.IGNORECASE), "UNION SELECT", 0.95),
    # Stacked queries / destructive
    (re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.IGNORECASE), "DROP statement", 0.95),
    (re.compile(r"\bINSERT\s+INTO\b", re.IGNORECASE), "INSERT INTO", 0.85),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), "DELETE FROM", 0.90),
    (re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE), "UPDATE SET", 0.85),
    # Time-based blind
    (re.compile(r"\bSLEEP\s*\(", re.IGNORECASE), "SLEEP() call", 0.90),
    (re.compile(r"\bBENCHMARK\s*\(", re.IGNORECASE), "BENCHMARK() call", 0.90),
    (re.compile(r"\bWAITFOR\s+DELAY\b", re.IGNORECASE), "WAITFOR DELAY", 0.90),
    # Comment-based evasion
    (re.compile(r"--\s"), "SQL comment (--)", 0.60),
    (re.compile(r"/\*.*?\*/"), "SQL block comment", 0.55),
    # Quote injection (standalone single-quote is very common in SQLi)
    (
        re.compile(r"'\s*(OR|AND|UNION|SELECT|DROP|INSERT|DELETE)\b", re.IGNORECASE),
        "quote + keyword",
        0.90,
    ),
    (
        re.compile(r";\s*(DROP|DELETE|INSERT|UPDATE|SELECT)\b", re.IGNORECASE),
        "semicolon + keyword",
        0.90,
    ),
    # Information schema probes
    (
        re.compile(r"\bINFORMATION_SCHEMA\b", re.IGNORECASE),
        "INFORMATION_SCHEMA probe",
        0.85,
    ),
    (re.compile(r"\bSYS\.(USER|DATABASE)\b", re.IGNORECASE), "sys object probe", 0.80),
    # Hex-encoded injection
    (re.compile(r"0x[0-9a-fA-F]{6,}"), "hex-encoded payload", 0.65),
]

# ---------------------------------------------------------------------------
# XSS patterns
# ---------------------------------------------------------------------------
_XSS_PATTERNS = [
    (re.compile(r"<\s*script\b", re.IGNORECASE), "<script> tag", 0.95),
    (re.compile(r"</\s*script\s*>", re.IGNORECASE), "</script> closing tag", 0.90),
    (re.compile(r"\bjavascript\s*:", re.IGNORECASE), "javascript: protocol", 0.90),
    (
        re.compile(
            r"\bon(error|load|click|mouseover|focus|blur|submit|change)\s*=",
            re.IGNORECASE,
        ),
        "event handler attribute",
        0.90,
    ),
    (re.compile(r"\beval\s*\(", re.IGNORECASE), "eval() call", 0.85),
    (
        re.compile(r"\bdocument\.(cookie|location|write)\b", re.IGNORECASE),
        "document.cookie/location/write",
        0.90,
    ),
    (
        re.compile(r"\bwindow\.(location|open)\b", re.IGNORECASE),
        "window.location/open",
        0.80,
    ),
    (re.compile(r"\balert\s*\(", re.IGNORECASE), "alert() call", 0.80),
    (re.compile(r"\bprompt\s*\(", re.IGNORECASE), "prompt() call", 0.75),
    (re.compile(r"\bconfirm\s*\(", re.IGNORECASE), "confirm() call", 0.70),
    (
        re.compile(r"<\s*img\b[^>]*\bon\w+\s*=", re.IGNORECASE),
        "<img> with event handler",
        0.90,
    ),
    (re.compile(r"<\s*iframe\b", re.IGNORECASE), "<iframe> tag", 0.85),
    (
        re.compile(r"<\s*svg\b[^>]*\bon\w+\s*=", re.IGNORECASE),
        "<svg> with event handler",
        0.90,
    ),
    (
        re.compile(r"<\s*body\b[^>]*\bon\w+\s*=", re.IGNORECASE),
        "<body> with event handler",
        0.90,
    ),
    # Data URI with base64
    (re.compile(r"data\s*:\s*text/html", re.IGNORECASE), "data:text/html URI", 0.80),
    # Expression in CSS (IE-specific but still probed)
    (re.compile(r"expression\s*\(", re.IGNORECASE), "CSS expression()", 0.75),
]

# ---------------------------------------------------------------------------
# Path traversal patterns
# ---------------------------------------------------------------------------
_PATH_TRAVERSAL_PATTERNS = [
    (re.compile(r"\.\./"), "../ traversal", 0.90),
    (re.compile(r"\.\.\\"), "..\ traversal", 0.90),
    (
        re.compile(r"%2e%2e[/%5c]", re.IGNORECASE),
        "URL-encoded traversal (%2e%2e)",
        0.90,
    ),
    (re.compile(r"%252e%252e", re.IGNORECASE), "double-encoded traversal", 0.95),
    (re.compile(r"/etc/passwd", re.IGNORECASE), "/etc/passwd access", 0.95),
    (re.compile(r"/etc/shadow", re.IGNORECASE), "/etc/shadow access", 0.95),
    (re.compile(r"/proc/self", re.IGNORECASE), "/proc/self access", 0.90),
    (
        re.compile(r"/proc/\d+/(cmdline|environ|fd)", re.IGNORECASE),
        "/proc/[pid] access",
        0.90,
    ),
    (
        re.compile(r"[/\\]windows[/\\]system32", re.IGNORECASE),
        "windows/system32 access",
        0.90,
    ),
    (re.compile(r"[/\\]boot\.ini", re.IGNORECASE), "boot.ini access", 0.85),
    (re.compile(r"[/\\]win\.ini", re.IGNORECASE), "win.ini access", 0.85),
]

# ---------------------------------------------------------------------------
# Scanner user-agent patterns
# ---------------------------------------------------------------------------
_SCANNER_UA_PATTERNS = [
    (re.compile(r"\bsqlmap\b", re.IGNORECASE), "sqlmap scanner", 0.95),
    (re.compile(r"\bnikto\b", re.IGNORECASE), "nikto scanner", 0.95),
    (re.compile(r"\bnmap\b", re.IGNORECASE), "nmap scanner", 0.90),
    (re.compile(r"\bdirbuster\b", re.IGNORECASE), "dirbuster scanner", 0.95),
    (re.compile(r"\bgobuster\b", re.IGNORECASE), "gobuster scanner", 0.95),
    (re.compile(r"\bwfuzz\b", re.IGNORECASE), "wfuzz scanner", 0.95),
    (re.compile(r"\bburpsuite\b", re.IGNORECASE), "burpsuite scanner", 0.90),
    (re.compile(r"\bhydra\b", re.IGNORECASE), "hydra brute-forcer", 0.90),
    (re.compile(r"\bmetasploit\b", re.IGNORECASE), "metasploit framework", 0.95),
    (re.compile(r"\bw3af\b", re.IGNORECASE), "w3af scanner", 0.90),
    (re.compile(r"\bzap\b", re.IGNORECASE), "OWASP ZAP", 0.80),
    (re.compile(r"\bmasscan\b", re.IGNORECASE), "masscan scanner", 0.90),
    (re.compile(r"\bferoxbuster\b", re.IGNORECASE), "feroxbuster scanner", 0.95),
]

# ---------------------------------------------------------------------------
# Directory enumeration — well-known sensitive paths
# ---------------------------------------------------------------------------
_DIR_ENUM_PATHS = [
    (re.compile(r"^/admin\b", re.IGNORECASE), "/admin probe", 0.80),
    (re.compile(r"^/wp-admin\b", re.IGNORECASE), "/wp-admin probe", 0.85),
    (re.compile(r"^/wp-login\b", re.IGNORECASE), "/wp-login probe", 0.85),
    (re.compile(r"^/wp-content\b", re.IGNORECASE), "/wp-content probe", 0.80),
    (re.compile(r"^/phpmyadmin\b", re.IGNORECASE), "/phpmyadmin probe", 0.90),
    (re.compile(r"^/pma\b", re.IGNORECASE), "/pma probe", 0.85),
    (re.compile(r"^/\.git\b", re.IGNORECASE), "/.git exposure", 0.90),
    (re.compile(r"^/\.env\b", re.IGNORECASE), "/.env exposure", 0.90),
    (re.compile(r"^/\.htaccess\b", re.IGNORECASE), "/.htaccess exposure", 0.85),
    (re.compile(r"^/\.htpasswd\b", re.IGNORECASE), "/.htpasswd exposure", 0.90),
    (re.compile(r"^/backup\b", re.IGNORECASE), "/backup probe", 0.75),
    (re.compile(r"^/config\b", re.IGNORECASE), "/config probe", 0.75),
    (re.compile(r"^/api/swagger\b", re.IGNORECASE), "/api/swagger probe", 0.70),
    (re.compile(r"^/swagger\b", re.IGNORECASE), "/swagger probe", 0.70),
    (re.compile(r"^/actuator\b", re.IGNORECASE), "/actuator probe", 0.85),
    (re.compile(r"^/debug\b", re.IGNORECASE), "/debug probe", 0.75),
    (re.compile(r"^/console\b", re.IGNORECASE), "/console probe", 0.75),
    (re.compile(r"^/server-status\b", re.IGNORECASE), "/server-status probe", 0.80),
    (re.compile(r"^/server-info\b", re.IGNORECASE), "/server-info probe", 0.80),
    (re.compile(r"^/cgi-bin\b", re.IGNORECASE), "/cgi-bin probe", 0.80),
    (re.compile(r"^/manager\b", re.IGNORECASE), "/manager probe (Tomcat)", 0.80),
    (re.compile(r"^/robots\.txt\b", re.IGNORECASE), "/robots.txt probe", 0.40),
    (re.compile(r"^/sitemap\.xml\b", re.IGNORECASE), "/sitemap.xml probe", 0.35),
    (re.compile(r"^/\.well-known\b", re.IGNORECASE), "/.well-known probe", 0.30),
    (re.compile(r"^/graphql\b", re.IGNORECASE), "/graphql probe", 0.65),
]


# ============================================================================
# AttackDetector — stateful detector with rate tracking
# ============================================================================
class AttackDetector:
    """
    Analyses HTTP request metadata and returns attack classifications.

    Maintains in-memory state for rate-based detections (brute force,
    reconnaissance scanning). State is lost on restart — acceptable for
    a real-time detection system where persistence isn't required.

    Parameters
    ----------
    brute_force_threshold : int
        Max requests to auth-like endpoints per IP within the window.
    brute_force_window : float
        Time window in seconds for brute force detection.
    scan_threshold : int
        Max unique paths per IP within the scan window.
    scan_window : float
        Time window in seconds for scan detection.
    """

    def __init__(
        self,
        brute_force_threshold=5,
        brute_force_window=30.0,
        scan_threshold=10,
        scan_window=15.0,
    ):
        self.brute_force_threshold = brute_force_threshold
        self.brute_force_window = brute_force_window
        self.scan_threshold = scan_threshold
        self.scan_window = scan_window

        # Rate tracking state: { ip: [(timestamp, path), ...] }
        self._auth_attempts = defaultdict(list)
        self._path_history = defaultdict(list)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def analyze(self, request_data):
        """
        Run all detection methods against a request and return findings.

        Parameters
        ----------
        request_data : dict
            Must contain: method, path, source_ip.
            Optional: headers (dict), body (str/dict), query_params (dict),
            timestamp (ISO string).

        Returns
        -------
        list[dict]
            List of attack findings. Empty list means no attack detected.
        """
        findings = []
        now = datetime.now(timezone.utc).isoformat()
        source_ip = request_data.get("source_ip", "unknown")

        # Build a summary for the raw_request_summary field
        summary = {
            "method": request_data.get("method", ""),
            "path": request_data.get("path", ""),
            "source_ip": source_ip,
            "user_agent": _extract_user_agent(request_data),
        }

        # Collect all text fields to scan for pattern-based detections
        scan_fields = _collect_scan_fields(request_data)

        # --- 1. SQL Injection ---
        findings.extend(self._detect_sqli(scan_fields, source_ip, now, summary))

        # --- 2. XSS ---
        findings.extend(self._detect_xss(scan_fields, source_ip, now, summary))

        # --- 3. Path Traversal ---
        findings.extend(
            self._detect_path_traversal(scan_fields, source_ip, now, summary)
        )

        # --- 4. Brute Force ---
        bf = self._detect_brute_force(request_data, source_ip, now, summary)
        if bf:
            findings.append(bf)

        # --- 5. Reconnaissance / Port Scanning ---
        findings.extend(self._detect_recon(request_data, source_ip, now, summary))

        # --- 6. Directory Enumeration ---
        findings.extend(self._detect_dir_enum(request_data, source_ip, now, summary))

        return findings

    # -----------------------------------------------------------------------
    # Detection methods
    # -----------------------------------------------------------------------
    def _detect_sqli(self, scan_fields, source_ip, timestamp, summary):
        """Check all request fields against SQL injection patterns."""
        findings = []
        seen = set()  # avoid duplicate findings from same pattern
        for text in scan_fields:
            for pattern, evidence, confidence in _SQLI_PATTERNS:
                if pattern.search(text) and evidence not in seen:
                    seen.add(evidence)
                    findings.append(
                        _make_finding(
                            "sqli",
                            confidence,
                            source_ip,
                            evidence,
                            timestamp,
                            summary,
                        )
                    )
        return findings

    def _detect_xss(self, scan_fields, source_ip, timestamp, summary):
        """Check all request fields against XSS patterns."""
        findings = []
        seen = set()
        for text in scan_fields:
            for pattern, evidence, confidence in _XSS_PATTERNS:
                if pattern.search(text) and evidence not in seen:
                    seen.add(evidence)
                    findings.append(
                        _make_finding(
                            "xss",
                            confidence,
                            source_ip,
                            evidence,
                            timestamp,
                            summary,
                        )
                    )
        return findings

    def _detect_path_traversal(self, scan_fields, source_ip, timestamp, summary):
        """Check for directory traversal patterns in path and parameters."""
        findings = []
        seen = set()
        for text in scan_fields:
            for pattern, evidence, confidence in _PATH_TRAVERSAL_PATTERNS:
                if pattern.search(text) and evidence not in seen:
                    seen.add(evidence)
                    findings.append(
                        _make_finding(
                            "path_traversal",
                            confidence,
                            source_ip,
                            evidence,
                            timestamp,
                            summary,
                        )
                    )
        return findings

    def _detect_brute_force(self, request_data, source_ip, timestamp, summary):
        """
        Track POST requests to auth-like endpoints per source IP.

        Auth-like endpoints: /api/cart/*/checkout, /login, /admin/login,
        /wp-login.php, /auth, /signin, /api/token.
        """
        method = request_data.get("method", "").upper()
        path = request_data.get("path", "")

        if method != "POST":
            return None

        # Check if path looks like an authentication endpoint
        auth_patterns = [
            re.compile(r"/api/cart/.+/checkout", re.IGNORECASE),
            re.compile(r"/login\b", re.IGNORECASE),
            re.compile(r"/admin/login\b", re.IGNORECASE),
            re.compile(r"/wp-login", re.IGNORECASE),
            re.compile(r"/auth\b", re.IGNORECASE),
            re.compile(r"/signin\b", re.IGNORECASE),
            re.compile(r"/api/token\b", re.IGNORECASE),
            re.compile(r"/api/v\d+/auth", re.IGNORECASE),
        ]

        is_auth = any(p.search(path) for p in auth_patterns)
        if not is_auth:
            return None

        now = time.monotonic()

        # Append this attempt
        self._auth_attempts[source_ip].append(now)

        # Purge entries outside the window
        cutoff = now - self.brute_force_window
        self._auth_attempts[source_ip] = [
            t for t in self._auth_attempts[source_ip] if t > cutoff
        ]

        count = len(self._auth_attempts[source_ip])
        if count >= self.brute_force_threshold:
            # Confidence scales with how far above the threshold we are
            confidence = min(0.60 + (count - self.brute_force_threshold) * 0.08, 0.98)
            return _make_finding(
                "brute_force",
                confidence,
                source_ip,
                f"{count} auth attempts in {self.brute_force_window}s to {path}",
                timestamp,
                summary,
            )

        return None

    def _detect_recon(self, request_data, source_ip, timestamp, summary):
        """
        Detect reconnaissance scanning: rapid unique path enumeration
        and known scanner user-agents.
        """
        findings = []
        path = request_data.get("path", "")
        user_agent = _extract_user_agent(request_data)

        # --- Scanner user-agent detection ---
        for pattern, evidence, confidence in _SCANNER_UA_PATTERNS:
            if pattern.search(user_agent):
                findings.append(
                    _make_finding(
                        "recon_scanner",
                        confidence,
                        source_ip,
                        f"Scanner UA detected: {evidence}",
                        timestamp,
                        summary,
                    )
                )
                # One scanner match is enough
                break

        # --- Rapid path enumeration ---
        now = time.monotonic()
        self._path_history[source_ip].append((now, path))

        # Purge entries outside the window
        cutoff = now - self.scan_window
        self._path_history[source_ip] = [
            (t, p) for t, p in self._path_history[source_ip] if t > cutoff
        ]

        unique_paths = len(set(p for _, p in self._path_history[source_ip]))
        if unique_paths >= self.scan_threshold:
            confidence = min(0.65 + (unique_paths - self.scan_threshold) * 0.05, 0.98)
            findings.append(
                _make_finding(
                    "recon_scanning",
                    confidence,
                    source_ip,
                    f"{unique_paths} unique paths in {self.scan_window}s",
                    timestamp,
                    summary,
                )
            )

        return findings

    def _detect_dir_enum(self, request_data, source_ip, timestamp, summary):
        """Check if the requested path matches known sensitive/admin endpoints."""
        path = request_data.get("path", "")
        findings = []

        for pattern, evidence, confidence in _DIR_ENUM_PATHS:
            if pattern.search(path):
                findings.append(
                    _make_finding(
                        "dir_enum",
                        confidence,
                        source_ip,
                        evidence,
                        timestamp,
                        summary,
                    )
                )
                # One directory enumeration match per request is sufficient
                break

        return findings

    # -----------------------------------------------------------------------
    # State maintenance
    # -----------------------------------------------------------------------
    def cleanup_stale_state(self, max_age=120.0):
        """
        Remove tracking entries older than max_age seconds.

        Call periodically (e.g. every 60s) to prevent unbounded memory
        growth from long-running deployments with many unique IPs.
        """
        cutoff = time.monotonic() - max_age

        stale_ips = []
        for ip, entries in self._auth_attempts.items():
            self._auth_attempts[ip] = [t for t in entries if t > cutoff]
            if not self._auth_attempts[ip]:
                stale_ips.append(ip)
        for ip in stale_ips:
            del self._auth_attempts[ip]

        stale_ips = []
        for ip, entries in self._path_history.items():
            self._path_history[ip] = [(t, p) for t, p in entries if t > cutoff]
            if not self._path_history[ip]:
                stale_ips.append(ip)
        for ip in stale_ips:
            del self._path_history[ip]

    def get_tracking_stats(self):
        """Return current state sizes for diagnostics."""
        return {
            "tracked_ips_auth": len(self._auth_attempts),
            "tracked_ips_paths": len(self._path_history),
            "total_auth_entries": sum(len(v) for v in self._auth_attempts.values()),
            "total_path_entries": sum(len(v) for v in self._path_history.values()),
        }


# ============================================================================
# Helper functions
# ============================================================================


def _make_finding(attack_type, confidence, source_ip, evidence, timestamp, summary):
    """Construct a standardised finding dict."""
    return {
        "attack_type": attack_type,
        "confidence": round(confidence, 2),
        "source_ip": source_ip,
        "evidence": evidence,
        "timestamp": timestamp,
        "raw_request_summary": summary,
    }


def _extract_user_agent(request_data):
    """Extract User-Agent from headers dict (case-insensitive lookup)."""
    headers = request_data.get("headers", {})
    if not headers:
        return ""
    # Headers may be any casing
    for key, value in headers.items():
        if key.lower() == "user-agent":
            return value
    return ""


def _collect_scan_fields(request_data):
    """
    Gather all text fields from the request that should be scanned
    for pattern-based attacks (SQLi, XSS, path traversal).

    Returns a list of strings. Empty/missing fields are skipped.
    """
    fields = []

    path = request_data.get("path", "")
    if path:
        fields.append(path)

    # Query parameters — flatten values
    query = request_data.get("query_params", {})
    if isinstance(query, dict):
        for v in query.values():
            if isinstance(v, list):
                fields.extend(str(item) for item in v)
            else:
                fields.append(str(v))
    elif isinstance(query, str) and query:
        fields.append(query)

    # Request body
    body = request_data.get("body", "")
    if isinstance(body, dict):
        # Flatten dict values for scanning
        for v in body.values():
            fields.append(str(v))
    elif isinstance(body, str) and body:
        fields.append(body)

    # Headers — scan all header values
    headers = request_data.get("headers", {})
    if isinstance(headers, dict):
        for v in headers.values():
            fields.append(str(v))

    return fields
