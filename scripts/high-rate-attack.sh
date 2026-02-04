#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${RED}========================================${NC}"
echo -e "${RED}  High Rate Attack Simulation          ${NC}"
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
echo -e "${YELLOW}Simulating high-rate attack (>50 req/min)...${NC}"
echo ""

# Configuration
TOTAL_REQUESTS=70
DELAY=0.05  # 50ms delay = ~1200 req/min (intentionally exceeds 50 req/min threshold)

echo -e "${YELLOW}Sending ${TOTAL_REQUESTS} requests in rapid succession...${NC}"
echo -e "${YELLOW}Rate: ~$(echo "scale=0; 60 / $DELAY" | bc) req/min (threshold: 50 req/min)${NC}"
echo ""

# Endpoints to target
ENDPOINTS=(
    "/api/products"
    "/api/cart"
    "/api/login"
    "/api/checkout"
    "/"
)

START_TIME=$(date +%s)

for i in $(seq 1 $TOTAL_REQUESTS); do
    # Randomly select endpoint
    ENDPOINT=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}

    # Send request
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${MANAGER_URL}${ENDPOINT}" 2>/dev/null)

    # Progress indicator
    if [ $((i % 10)) -eq 0 ]; then
        echo -e "${GREEN}[${i}/${TOTAL_REQUESTS}] Sent requests (last: ${ENDPOINT}, HTTP ${HTTP_CODE})${NC}"
    fi

    # Small delay
    sleep $DELAY
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
RATE=$(echo "scale=2; $TOTAL_REQUESTS / ($ELAPSED / 60)" | bc)

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Attack Summary                        ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Total requests sent: ${TOTAL_REQUESTS}${NC}"
echo -e "${GREEN}Time elapsed: ${ELAPSED} seconds${NC}"
echo -e "${GREEN}Actual rate: ${RATE} req/min${NC}"
echo -e "${YELLOW}Threshold: 50 req/min${NC}"
echo ""

if (( $(echo "$RATE > 50" | bc -l) )); then
    echo -e "${RED}✓ Rate limit threshold EXCEEDED${NC}"
    echo -e "${YELLOW}Sentinel should detect rate limit violation.${NC}"
else
    echo -e "${YELLOW}! Rate limit threshold NOT exceeded (try increasing TOTAL_REQUESTS)${NC}"
fi
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
