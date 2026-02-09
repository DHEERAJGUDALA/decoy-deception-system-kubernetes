#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-http://localhost:30080}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIMULATOR="${SCRIPT_DIR}/simulate_attacks.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BLUE='\033[0;34m'
WHITE='\033[1;37m'
NC='\033[0m'

banner() {
    echo ""
    echo -e "${WHITE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${WHITE}║     🛡️  DECEPTION SYSTEM — 3-Minute Demo Scenario      ║${NC}"
    echo -e "${WHITE}║     Target: ${TARGET}                        ║${NC}"
    echo -e "${WHITE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

narrate() {
    echo ""
    echo -e "${WHITE}>> [$1] $2${NC}"
    echo ""
}

countdown() {
    local secs=$1
    local msg=$2
    for i in $(seq "$secs" -1 1); do
        printf "\r${CYAN}  ⏳ %s in %ds...${NC}  " "$msg" "$i"
        sleep 1
    done
    printf "\r%-60s\r" " "
}

cleanup() {
    echo ""
    narrate "CLEANUP" "Stopping background processes..."
    if [ -n "${LEGIT_PID:-}" ]; then
        kill "$LEGIT_PID" 2>/dev/null || true
        wait "$LEGIT_PID" 2>/dev/null || true
    fi
    echo -e "${GREEN}Done.${NC}"
}

trap cleanup EXIT

banner

narrate "0:00" "Step 1 — Starting legitimate user traffic (background)"
echo -e "${GREEN}  Normal shopping behavior: browse, add to cart, checkout${NC}"
echo -e "${GREEN}  User IP: 10.0.0.50${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type legitimate --continuous &
LEGIT_PID=$!
sleep 2

countdown 13 "Next attack wave"

narrate "0:15" "Step 2 — Launching SQL Injection attack"
echo -e "${RED}  >> Watch the dashboard — SQLi attack detected, 3 decoys spawning...${NC}"
echo -e "${RED}  Attacker IP: 192.168.1.66${NC}"
echo -e "${RED}  Payloads: UNION SELECT, time-based blind SQLi, tautologies${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type sqli

countdown 15 "Next attack wave"

narrate "0:45" "Step 3 — Launching XSS attack from different IP"
echo -e "${YELLOW}  >> Watch the dashboard — XSS from new IP, another decoy set spawning...${NC}"
echo -e "${YELLOW}  Attacker IP: 192.168.1.77${NC}"
echo -e "${YELLOW}  Payloads: script tags, event handlers, data URIs${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type xss

countdown 15 "Next attack wave"

narrate "1:15" "Step 4 — Launching reconnaissance scanner"
echo -e "${BLUE}  >> Watch the dashboard — Scanner detected (sqlmap UA), paths enumerated...${NC}"
echo -e "${BLUE}  Attacker IP: 192.168.1.55${NC}"
echo -e "${BLUE}  User-Agent: sqlmap/1.5 — scanning 30+ paths${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type recon

countdown 15 "Next attack wave"

narrate "1:45" "Step 5 — Launching brute force attack"
echo -e "${CYAN}  >> Watch the dashboard — Rapid POST requests, rate limiting + brute force detection...${NC}"
echo -e "${CYAN}  Attacker IP: 192.168.1.99${NC}"
echo -e "${CYAN}  20 requests in 5 seconds${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type bruteforce

countdown 15 "Next attack wave"

narrate "2:15" "Step 6 — Launching directory traversal"
echo -e "${MAGENTA}  >> Watch the dashboard — Path traversal + sensitive file probing detected...${NC}"
echo -e "${MAGENTA}  Attacker IP: 192.168.1.88${NC}"
echo -e "${MAGENTA}  Probing: /etc/passwd, .env, .git/config, /admin, /wp-login.php${NC}"
python3 "$SIMULATOR" --target "$TARGET" --attack-type traversal

countdown 15 "Waiting for TTL cleanup"

narrate "2:45" "Step 7 — Decoy TTL expiration"
echo -e "${WHITE}  >> Watch decoys being cleaned up after TTL expires...${NC}"
echo -e "${WHITE}  The deception-controller checks TTL every 60s${NC}"
echo -e "${WHITE}  Decoy pods will be removed and routes cleared${NC}"
sleep 15

narrate "3:00" "Step 8 — Demo Summary"
echo ""
echo -e "${WHITE}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${WHITE}║                    📊 Demo Summary                      ║${NC}"
echo -e "${WHITE}╠══════════════════════════════════════════════════════════╣${NC}"
echo -e "${RED}║  SQLi Attack      → IP 192.168.1.66 → Decoy spawned   ║${NC}"
echo -e "${YELLOW}║  XSS Attack       → IP 192.168.1.77 → Decoy spawned   ║${NC}"
echo -e "${BLUE}║  Recon Scanner    → IP 192.168.1.55 → Decoy spawned   ║${NC}"
echo -e "${CYAN}║  Brute Force      → IP 192.168.1.99 → Decoy spawned   ║${NC}"
echo -e "${MAGENTA}║  Dir Traversal    → IP 192.168.1.88 → Decoy spawned   ║${NC}"
echo -e "${GREEN}║  Legitimate User  → IP 10.0.0.50    → Real services   ║${NC}"
echo -e "${WHITE}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${WHITE}Querying system status...${NC}"
echo ""

echo -e "${CYAN}--- Deception Controller Status ---${NC}"
curl -s "${TARGET%:30080}:8086/status" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (controller not reachable)"
echo ""

echo -e "${CYAN}--- Traffic Analyzer Stats ---${NC}"
curl -s "${TARGET%:30080}:8085/stats" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  (analyzer not reachable)"
echo ""

echo -e "${WHITE}Dashboard: http://localhost:30088${NC}"
echo -e "${WHITE}Demo complete! 🎉${NC}"
