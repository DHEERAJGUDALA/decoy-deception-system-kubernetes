#!/bin/bash
# update-route.sh — Manually add or remove attacker routes for testing.
#
# Usage:
#   Add route:    ./update-route.sh add  <attacker_ip> <decoy_frontend_url>
#   Remove route: ./update-route.sh remove <attacker_ip>
#   List routes:  ./update-route.sh list
#
# Examples:
#   ./update-route.sh add 192.168.1.100 decoy-fe-abc12345.decoy-pool.svc.cluster.local:3000
#   ./update-route.sh remove 192.168.1.100
#   ./update-route.sh list
#
# Environment:
#   ROUTER_URL  — Base URL of the traffic router (default: http://localhost:30080)

set -euo pipefail

ROUTER_URL="${ROUTER_URL:-http://localhost:30080}"

usage() {
    echo "Usage:"
    echo "  $0 add    <attacker_ip> <decoy_frontend_url>"
    echo "  $0 remove <attacker_ip>"
    echo "  $0 list"
    exit 1
}

fmt_json() {
    if command -v python3 &>/dev/null; then
        python3 -m json.tool 2>/dev/null || cat
    elif command -v jq &>/dev/null; then
        jq . 2>/dev/null || cat
    else
        cat
    fi
}

case "${1:-}" in
    add)
        IP="${2:?Error: attacker_ip required}"
        URL="${3:?Error: decoy_frontend_url required}"
        echo "Adding route: ${IP} -> ${URL}"
        curl -sS -X POST "${ROUTER_URL}/internal/add-route" \
            -H "Content-Type: application/json" \
            -d "{\"attacker_ip\":\"${IP}\",\"decoy_frontend_url\":\"${URL}\"}" \
            --connect-timeout 5 \
            --max-time 10 | fmt_json
        echo
        ;;
    remove)
        IP="${2:?Error: attacker_ip required}"
        echo "Removing route: ${IP}"
        curl -sS -X POST "${ROUTER_URL}/internal/remove-route" \
            -H "Content-Type: application/json" \
            -d "{\"attacker_ip\":\"${IP}\"}" \
            --connect-timeout 5 \
            --max-time 10 | fmt_json
        echo
        ;;
    list)
        echo "Current routes:"
        curl -sS "${ROUTER_URL}/internal/routes" \
            --connect-timeout 5 \
            --max-time 10 | fmt_json
        echo
        ;;
    *)
        usage
        ;;
esac
