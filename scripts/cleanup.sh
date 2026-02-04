#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Decoy Deception System - Cleanup     ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Confirm cleanup
read -p "$(echo -e ${YELLOW}This will delete all deployments. Continue? [y/N]: ${NC})" -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Cleanup cancelled.${NC}"
    exit 0
fi

echo -e "${BLUE}[1/6] Deleting AppGraph CRs...${NC}"
if kubectl get appgraph &> /dev/null; then
    kubectl delete appgraph --all --timeout=60s > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ AppGraph CRs deleted${NC}"
else
    echo -e "${YELLOW}No AppGraph CRs found${NC}"
fi
echo ""

echo -e "${BLUE}[2/6] Deleting services...${NC}"
SERVICES=(
    "controller"
    "sentinel"
    "manager"
    "frontend-api"
    "payment-svc"
    "reporter"
)

for service in "${SERVICES[@]}"; do
    if kubectl get deployment "${service}" &> /dev/null; then
        kubectl delete deployment "${service}" --timeout=60s > /dev/null 2>&1 || true
        echo -e "${GREEN}✓ Deleted deployment: ${service}${NC}"
    fi

    if kubectl get service "${service}" &> /dev/null; then
        kubectl delete service "${service}" --timeout=30s > /dev/null 2>&1 || true
        echo -e "${GREEN}✓ Deleted service: ${service}${NC}"
    fi
done
echo ""

echo -e "${BLUE}[3/6] Deleting ConfigMaps...${NC}"
if kubectl get configmap sentinel-config &> /dev/null; then
    kubectl delete configmap sentinel-config > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ Deleted ConfigMap: sentinel-config${NC}"
fi
echo ""

echo -e "${BLUE}[4/6] Deleting RBAC resources...${NC}"
RBAC_RESOURCES=(
    "serviceaccount:sentinel"
    "serviceaccount:controller"
    "role:sentinel-role"
    "rolebinding:sentinel-rolebinding"
    "clusterrole:controller-role"
    "clusterrolebinding:controller-rolebinding"
)

for resource in "${RBAC_RESOURCES[@]}"; do
    IFS=':' read -r kind name <<< "$resource"
    if kubectl get "${kind}" "${name}" &> /dev/null 2>&1; then
        kubectl delete "${kind}" "${name}" --timeout=30s > /dev/null 2>&1 || true
        echo -e "${GREEN}✓ Deleted ${kind}: ${name}${NC}"
    fi
done
echo ""

echo -e "${BLUE}[5/6] Deleting decoy pods and resources...${NC}"
# Delete any decoy pods
kubectl delete pods -l decoy=true --timeout=60s > /dev/null 2>&1 || true
# Delete any decoy services
kubectl delete services -l decoy=true --timeout=30s > /dev/null 2>&1 || true
# Delete any decoy network policies
kubectl delete networkpolicies -l decoy=true --timeout=30s > /dev/null 2>&1 || true
echo -e "${GREEN}✓ Decoy resources cleaned${NC}"
echo ""

echo -e "${BLUE}[6/6] Deleting AppGraph CRD...${NC}"
if kubectl get crd appgraphs.deception.k8s.io &> /dev/null; then
    kubectl delete crd appgraphs.deception.k8s.io --timeout=60s > /dev/null 2>&1 || true
    echo -e "${GREEN}✓ AppGraph CRD deleted${NC}"
fi
echo ""

# Wait for all pods to terminate
echo -e "${YELLOW}Waiting for pods to terminate...${NC}"
kubectl wait --for=delete pod -l app=controller --timeout=60s > /dev/null 2>&1 || true
kubectl wait --for=delete pod -l app=sentinel --timeout=60s > /dev/null 2>&1 || true
kubectl wait --for=delete pod -l app=manager --timeout=60s > /dev/null 2>&1 || true
kubectl wait --for=delete pod -l app=frontend-api --timeout=60s > /dev/null 2>&1 || true
kubectl wait --for=delete pod -l app=payment-svc --timeout=60s > /dev/null 2>&1 || true
kubectl wait --for=delete pod -l app=reporter --timeout=60s > /dev/null 2>&1 || true
echo ""

# Display final status
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}         Cleanup Complete! ✓           ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

echo -e "${GREEN}Remaining pods:${NC}"
kubectl get pods
echo ""

echo -e "${YELLOW}Note: Docker images are still available locally.${NC}"
echo -e "${YELLOW}To remove them, run: make clean-images${NC}"
echo ""
