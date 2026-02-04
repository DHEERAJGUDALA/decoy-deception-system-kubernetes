# Deployment and Testing Scripts

This directory contains automation scripts for deploying and testing the Decoy Deception System.

## Deployment Scripts

### deploy-all.sh
Automated deployment script that:
1. Checks prerequisites (docker, kubectl, k3s)
2. Builds all Docker images
3. Saves and imports images into k3s (WSL-compatible)
4. Deploys AppGraph CRD, RBAC, ConfigMaps
5. Deploys all services in order
6. Waits for pods to be ready
7. Displays deployment status and endpoints

**Usage:**
```bash
bash scripts/deploy-all.sh
# or
make deploy
```

**Prerequisites:**
- k3s running on WSL
- Docker installed
- kubectl configured

**Expected Output:**
- All 6 services deployed (reporter, payment-svc, frontend-api, manager, sentinel, controller)
- Manager accessible at http://NODE_IP:30000
- Controller dashboard at http://NODE_IP:30090

**Critical Implementation:**
The script uses `docker save` and `k3s ctr images import` to transfer images from Docker to k3s, which is required in WSL environments where k3s cannot directly access Docker's image store.

### cleanup.sh
Cleanup script that removes all deployments:
1. Deletes AppGraph custom resources
2. Removes all service deployments
3. Deletes ConfigMaps
4. Removes RBAC resources
5. Cleans up decoy pods and NetworkPolicies
6. Deletes AppGraph CRD

**Usage:**
```bash
bash scripts/cleanup.sh
# or
make clean-deploy
```

**Confirmation:**
Script prompts for confirmation before proceeding (y/N).

**Note:** Docker images remain available locally after cleanup. Use `make clean-images` to remove them.

## Attack Simulation Scripts

### sql-injection-attack.sh
Simulates SQL injection attacks against the Manager endpoint.

**Attack Vectors:**
- 10 different SQLi payloads
- Targets 3 endpoints: /api/products, /api/login, /api/cart
- Total: 30 attack attempts
- URL-encoded payloads

**Expected Behavior:**
- Sentinel detects SQLi patterns in logs
- Controller receives alert from Sentinel
- Manager blocks attacker IP
- AppGraph creates 3 decoy pods
- Attacker routed to decoys in round-robin

**Usage:**
```bash
bash scripts/sql-injection-attack.sh
# or
make test-sqli
```

**Verification:**
```bash
# Check Sentinel detection
kubectl logs -l app=sentinel -f

# Check Manager IP blocking
kubectl logs -l app=manager | grep block_ip

# View dashboard
make dashboard
```

### high-rate-attack.sh
Simulates rate limit violation with rapid requests.

**Attack Configuration:**
- 70 requests in rapid succession
- ~1200 req/min rate (exceeds 50 req/min threshold)
- Randomized endpoints
- 50ms delay between requests

**Expected Behavior:**
- Sentinel detects rate limit exceeded (>50 req/min)
- Alert sent to Controller
- IP blocked and routed to decoys

**Usage:**
```bash
bash scripts/high-rate-attack.sh
# or
make test-rate
```

**Rate Calculation:**
Script displays actual request rate and compares to threshold (50 req/min).

### normal-traffic.sh
Simulates legitimate user traffic.

**Traffic Pattern:**
- 20 requests total
- 3-second delay between requests (~20 req/min)
- Normal user flow: Homepage → Products → Cart → Login → Checkout
- No attack patterns

**Expected Behavior:**
- NO alerts from Sentinel
- Traffic routed to legitimate frontend-api
- Metrics collected by Reporter
- Normal HTTP 200 responses

**Usage:**
```bash
bash scripts/normal-traffic.sh
# or
make test-normal
```

**Verification:**
```bash
# Check Reporter stats
kubectl port-forward svc/reporter 8080:8080
curl http://localhost:8080/api/stats | jq
```

## Makefile Integration

All scripts are integrated into the Makefile for easy access:

### Build & Deploy
```bash
make build         # Build all Docker images
make deploy        # Deploy all services
make clean-deploy  # Remove deployments
make clean-images  # Remove Docker images
```

### Testing
```bash
make test          # Run all attack simulations
make test-normal   # Normal traffic only
make test-sqli     # SQL injection only
make test-rate     # Rate limit attack only
```

### Monitoring
```bash
make dashboard     # Open Controller dashboard
make logs          # Tail Sentinel logs
```

## End-to-End Testing Workflow

### 1. Deploy System
```bash
# Setup k3s (first time only)
make setup
make verify

# Build and deploy
make build
make deploy
```

### 2. Verify Deployment
```bash
# Check all pods running
kubectl get pods

# Check services
kubectl get svc

# Open dashboard
make dashboard
```

### 3. Run Normal Traffic
```bash
# Baseline traffic
make test-normal

# Verify metrics
kubectl port-forward svc/reporter 8080:8080
curl http://localhost:8080/api/stats | jq
```

### 4. Simulate Attacks
```bash
# SQL injection attack
make test-sqli

# Watch Sentinel logs in another terminal
kubectl logs -l app=sentinel -f

# Verify decoys created
kubectl get pods -l decoy=true

# Check dashboard for topology
make dashboard
```

### 5. Rate Limit Attack
```bash
# High-rate attack
make test-rate

# Verify rate detection
kubectl logs -l app=sentinel | grep rate_limit_exceeded
```

### 6. Monitor System
```bash
# View Sentinel logs
make logs

# Check Manager routing decisions
kubectl logs -l app=manager

# View Controller events
kubectl logs -l app=controller

# Check Reporter metrics
kubectl port-forward svc/reporter 8080:8080
curl http://localhost:8080/api/stats | jq
```

### 7. Cleanup
```bash
# Remove deployments
make clean-deploy

# Remove images (optional)
make clean-images

# Uninstall k3s (optional)
make clean
```

## WSL-Specific Considerations

### Image Transfer
k3s in WSL cannot directly access Docker's image store. The `deploy-all.sh` script handles this by:
1. Building images with Docker
2. Saving images to tar files
3. Importing tar files into k3s using `k3s ctr images import`

### Browser Access
The `make dashboard` target attempts to open the browser using:
- `wslview` (WSL-specific tool)
- `xdg-open` (Linux fallback)
- Manual URL display if neither available

### Network Access
WSL uses a virtual network. Access services via:
- Node IP (internal WSL network)
- NodePort services (30000, 30090)

## Troubleshooting

### Images Not Found
If pods show `ErrImagePull`:
```bash
# Verify images in k3s
sudo k3s ctr images ls | grep -E 'frontend-api|payment-svc|manager|sentinel|controller|reporter'

# Re-run deployment
make deploy
```

### Pods Not Ready
```bash
# Check pod status
kubectl get pods

# View pod logs
kubectl logs <pod-name>

# Describe pod for events
kubectl describe pod <pod-name>
```

### Attack Not Detected
```bash
# Verify Sentinel is running
kubectl get pods -l app=sentinel

# Check Sentinel logs
kubectl logs -l app=sentinel -f

# Verify ConfigMap
kubectl get configmap sentinel-config -o yaml
```

### Dashboard Not Accessible
```bash
# Get node IP
kubectl get nodes -o wide

# Verify Controller service
kubectl get svc controller

# Port-forward if NodePort issue
kubectl port-forward svc/controller 8090:8080
# Access at http://localhost:8090
```

## Script Outputs

All scripts provide colored output:
- **Blue**: Section headers
- **Green**: Success messages
- **Yellow**: Warnings and info
- **Red**: Errors and attack simulations

Progress indicators show:
- Current step / total steps
- Individual service status
- HTTP response codes
- Attack counts
- Resource creation status

## Performance Notes

### Deployment Time
- Image build: ~2-3 minutes (6 services)
- Image import: ~30 seconds
- Pod startup: ~30-60 seconds
- Total: ~4-5 minutes

### Attack Simulation Time
- SQL injection: ~10 seconds (30 requests)
- High-rate: ~5 seconds (70 requests)
- Normal traffic: ~60 seconds (20 requests with 3s delay)

### Cleanup Time
- Deployment removal: ~2 minutes
- Image removal: ~10 seconds
- Full k3s uninstall: ~30 seconds
