#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Decoy Deception System - Deploy All  ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if running in WSL
if ! grep -qi microsoft /proc/version; then
    echo -e "${YELLOW}Warning: Not running in WSL. Some steps may differ.${NC}"
fi

# Check prerequisites
echo -e "${BLUE}[1/8] Checking prerequisites...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: docker not found${NC}"
    exit 1
fi

if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}Error: kubectl not found${NC}"
    exit 1
fi

if ! sudo k3s kubectl get nodes &> /dev/null; then
    echo -e "${RED}Error: k3s not running or not accessible${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Prerequisites OK${NC}"
echo ""

# Build Docker images
echo -e "${BLUE}[2/8] Building Docker images...${NC}"
SERVICES=(
    "frontend-api:services/frontend-api"
    "payment-svc:services/payment-svc"
    "manager:services/manager"
    "sentinel:services/sentinel"
    "controller:services/controller"
    "reporter:services/reporter"
)

for service_def in "${SERVICES[@]}"; do
    IFS=':' read -r service_name service_path <<< "$service_def"
    echo -e "${YELLOW}Building ${service_name}...${NC}"

    if [ -f "${service_path}/Dockerfile" ]; then
        docker build -t "${service_name}:latest" "${service_path}" > /dev/null 2>&1
        echo -e "${GREEN}✓ Built ${service_name}${NC}"
    else
        echo -e "${RED}Error: Dockerfile not found at ${service_path}${NC}"
        exit 1
    fi
done
echo ""

# Save Docker images and import into k3s
echo -e "${BLUE}[3/8] Importing images into k3s...${NC}"
TEMP_TAR="/tmp/decoy-images.tar"

for service_def in "${SERVICES[@]}"; do
    IFS=':' read -r service_name service_path <<< "$service_def"
    echo -e "${YELLOW}Importing ${service_name} into k3s...${NC}"

    # Save image to tar
    docker save "${service_name}:latest" -o "${TEMP_TAR}" > /dev/null 2>&1

    # Import into k3s
    sudo k3s ctr images import "${TEMP_TAR}" > /dev/null 2>&1

    # Verify import
    if sudo k3s ctr images ls | grep -q "${service_name}"; then
        echo -e "${GREEN}✓ Imported ${service_name} into k3s${NC}"
    else
        echo -e "${RED}Error: Failed to import ${service_name}${NC}"
        exit 1
    fi
done

# Cleanup temp tar
rm -f "${TEMP_TAR}"
echo ""

# Deploy AppGraph CRD
echo -e "${BLUE}[4/8] Deploying AppGraph CRD...${NC}"
kubectl apply -f deploy/k8s/appgraph-crd.yaml > /dev/null 2>&1
echo -e "${GREEN}✓ AppGraph CRD deployed${NC}"
echo ""

# Deploy RBAC
echo -e "${BLUE}[5/8] Deploying RBAC configurations...${NC}"
kubectl apply -f deploy/k8s/sentinel-rbac.yaml > /dev/null 2>&1
kubectl apply -f deploy/k8s/controller-rbac.yaml > /dev/null 2>&1
echo -e "${GREEN}✓ RBAC configurations deployed${NC}"
echo ""

# Deploy ConfigMaps
echo -e "${BLUE}[6/8] Deploying ConfigMaps...${NC}"
kubectl apply -f deploy/k8s/sentinel-configmap.yaml > /dev/null 2>&1
echo -e "${GREEN}✓ ConfigMaps deployed${NC}"
echo ""

# Deploy services in order
echo -e "${BLUE}[7/8] Deploying services...${NC}"

DEPLOY_ORDER=(
    "reporter:deploy/k8s/reporter.yaml"
    "payment-svc:deploy/k8s/payment-svc.yaml"
    "frontend-api:deploy/k8s/frontend-api.yaml"
    "manager:deploy/k8s/manager.yaml"
    "sentinel:deploy/k8s/sentinel.yaml"
    "controller:deploy/k8s/controller.yaml"
)

for deploy_def in "${DEPLOY_ORDER[@]}"; do
    IFS=':' read -r service_name deploy_file <<< "$deploy_def"
    echo -e "${YELLOW}Deploying ${service_name}...${NC}"

    kubectl apply -f "${deploy_file}" > /dev/null 2>&1
    echo -e "${GREEN}✓ ${service_name} deployed${NC}"
done
echo ""

# Wait for pods to be ready
echo -e "${BLUE}[8/8] Waiting for pods to be ready...${NC}"
echo -e "${YELLOW}This may take 30-60 seconds...${NC}"

PODS=(
    "app=reporter"
    "app=payment-svc"
    "app=frontend-api"
    "app=manager"
    "app=sentinel"
    "app=controller"
)

for pod_label in "${PODS[@]}"; do
    echo -e "${YELLOW}Waiting for ${pod_label}...${NC}"
    kubectl wait --for=condition=ready pod -l "${pod_label}" --timeout=120s > /dev/null 2>&1
    echo -e "${GREEN}✓ ${pod_label} ready${NC}"
done
echo ""

# Display deployment status
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}       Deployment Complete! ✓          ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

echo -e "${GREEN}Service Status:${NC}"
kubectl get pods
echo ""

echo -e "${GREEN}Service Endpoints:${NC}"
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
echo -e "  Manager (Entry Point):  http://${NODE_IP}:30000"
echo -e "  Controller Dashboard:   http://${NODE_IP}:30090"
echo ""

echo -e "${YELLOW}Next Steps:${NC}"
echo -e "  1. Access dashboard: make dashboard"
echo -e "  2. Run normal traffic: make test-normal"
echo -e "  3. Simulate attacks: make test-attack"
echo -e "  4. View logs: kubectl logs -l app=sentinel -f"
echo ""
