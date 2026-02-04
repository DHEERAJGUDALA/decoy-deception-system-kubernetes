#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Normal Traffic Simulation            ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Get manager endpoint
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)
MANAGER_URL="http://${NODE_IP}:30000"

if [ -z "$NODE_IP" ]; then
    echo -e "${YELLOW}Error: Could not get node IP. Is k3s running?${NC}"
    exit 1
fi

echo -e "${BLUE}Target: ${MANAGER_URL}${NC}"
echo -e "${YELLOW}Simulating normal user traffic...${NC}"
echo ""

# Configuration
TOTAL_REQUESTS=20
DELAY=3  # 3 seconds between requests = 20 req/min (under 50 req/min threshold)

echo -e "${YELLOW}Sending ${TOTAL_REQUESTS} requests with ${DELAY}s delay...${NC}"
echo -e "${YELLOW}Rate: ~$(echo "scale=0; 60 / $DELAY" | bc) req/min (threshold: 50 req/min)${NC}"
echo ""

# Simulate normal user flow
USER_FLOWS=(
    "Homepage:/:"
    "Browse Products:/api/products"
    "Add to Cart:/api/cart"
    "View Products Again:/api/products"
    "Login:/api/login"
    "Checkout:/api/checkout"
)

REQUEST_COUNT=0

for flow_def in "${USER_FLOWS[@]}"; do
    IFS=':' read -r step_name endpoint <<< "$flow_def"

    # Repeat each step multiple times to reach total requests
    for i in $(seq 1 3); do
        if [ $REQUEST_COUNT -ge $TOTAL_REQUESTS ]; then
            break 2
        fi

        REQUEST_COUNT=$((REQUEST_COUNT + 1))

        # Send request
        RESPONSE=$(curl -s -w "\n%{http_code}" "${MANAGER_URL}${endpoint}" 2>/dev/null)
        HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

        if [ "$HTTP_CODE" == "200" ]; then
            echo -e "${GREEN}[${REQUEST_COUNT}/${TOTAL_REQUESTS}] ${step_name} - HTTP ${HTTP_CODE} ✓${NC}"
        else
            echo -e "${YELLOW}[${REQUEST_COUNT}/${TOTAL_REQUESTS}] ${step_name} - HTTP ${HTTP_CODE}${NC}"
        fi

        # Normal user delay
        sleep $DELAY
    done
done

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Traffic Summary                       ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Total requests sent: ${REQUEST_COUNT}${NC}"
echo -e "${GREEN}Rate: ~20 req/min (normal traffic)${NC}"
echo -e "${YELLOW}This traffic should NOT trigger alerts.${NC}"
echo ""

echo -e "${YELLOW}Check Reporter stats:${NC}"
echo -e "  kubectl port-forward svc/reporter 8080:8080"
echo -e "  curl http://localhost:8080/api/stats | jq"
echo ""

echo -e "${YELLOW}View Controller dashboard:${NC}"
echo -e "  http://${NODE_IP}:30090"
echo ""
