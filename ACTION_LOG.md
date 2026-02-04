# Action Log - Decoy Deception System

This file tracks the development progress of the decoy-deception-system project.

---

## Phase 1: Setup
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Initial project structure created with k3s installation and verification scripts for WSL environments. The setup is optimized for low-memory operation with a target of <800MB for k3s.

### Files Created

#### Project Root
- `README.md` - Project documentation with system requirements, quick start guide, and architecture overview
- `Makefile` - Build automation with targets: `check`, `setup`, `verify`, `clean`

#### setup/
- `install-k3s-wsl.sh` - k3s installation script with WSL detection and low-memory configuration
  - Disables: traefik, servicelb, metrics-server
  - Sets API server verbosity to v=2
  - Configures ~/.kube/config automatically
- `verify-install.sh` - Verification script that checks cluster status and k3s memory usage

#### deploy/
- Empty directory (reserved for future deployment manifests)

### Key Features
- **WSL Compatibility**: Fail-fast WSL detection in installation script
- **Memory Optimization**: k3s configured with minimal components (<800MB target)
- **Automation**: Makefile targets for checking dependencies, setup, verification, and cleanup
- **Verification**: Automated memory usage reporting via RSS measurement

### Memory Budget
- Total RAM budget: 2.5GB
- k3s target: <800MB
- Configuration: Minimal k3s with disabled non-essential services

### Dependencies Required
- WSL (Windows Subsystem for Linux)
- Go (checked by `make check`)
- Docker or nerdctl (checked by `make check`)

---

## Phase 2: Decoy Services
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Implemented two lightweight Go microservices (frontend-api and payment-svc) with configurable decoy behavior. Both services support in-memory operation with no database dependencies and can be toggled between normal and decoy modes via environment variables.

### Files Created

#### services/frontend-api/
- `go.mod` - Go module definition
- `cmd/main.go` - Main service implementation (370 lines)
  - Endpoints: /health, /api/products, /api/cart, /api/login, /api/checkout
  - Embedded static HTML homepage
  - JSON structured logging with timestamp, method, path, source_ip
  - Async metrics push to reporter-service (fire-and-forget)
  - Payment service integration
- `Dockerfile` - Multi-stage Alpine build (CGO disabled, ~15-20MB image)

#### services/payment-svc/
- `go.mod` - Go module definition
- `cmd/main.go` - Payment service implementation (160 lines)
  - Endpoints: /health, /api/charge
  - JSON structured logging
  - Simulated payment processing with transaction IDs
- `Dockerfile` - Multi-stage Alpine build (CGO disabled, ~12-15MB image)

#### deploy/k8s/
- `frontend-api.yaml` - Kubernetes Deployment + Service
  - Resource limits: 80Mi memory, 50m CPU
  - Liveness/readiness probes on /health
  - Environment variables for decoy configuration
- `payment-svc.yaml` - Kubernetes Deployment + Service
  - Resource limits: 40Mi memory, 30m CPU
  - Liveness/readiness probes on /health

#### services/
- `BUILD.md` - Comprehensive build and deployment instructions
  - Local development commands
  - Docker/nerdctl build instructions
  - Kubernetes deployment procedures
  - Decoy configuration examples

### Decoy Behavior System

Both services support three decoy modes controlled by environment variables:

**Environment Variables**:
- `IS_DECOY` - Enable/disable decoy mode (true/false)
- `DECOY_TYPE` - Behavior type: exact|slow|logger
- `DECOY_LATENCY` - Artificial latency in milliseconds (for slow mode)
- `DECOY_LOGGING` - Log verbosity: normal|verbose

**Decoy Types**:
1. **exact**: Normal behavior, indistinguishable from production
2. **slow**: Adds configurable latency to all requests
3. **logger**: Verbose logging of all request/response data

### Service Architecture

**frontend-api** (Port 8080):
- Static HTML homepage showing endpoint documentation
- Product catalog (4 mock products in-memory)
- Cart management endpoint
- Login endpoint (returns mock JWT token)
- Checkout endpoint (calls payment-svc)
- Structured JSON logging on every request
- Async metrics reporting to reporter-service
- Extracts source IP from X-Forwarded-For header

**payment-svc** (Port 8081):
- Payment charge endpoint
- Generates unique transaction IDs using Unix nanoseconds (base36)
- Structured JSON logging
- Support for same decoy behaviors as frontend-api

### Docker Images
- Both images use multi-stage builds
- Builder stage: golang:1.21-alpine
- Final stage: alpine:latest with ca-certificates
- CGO disabled for static binaries
- Stripped binaries (-ldflags="-w -s")
- Non-root user (appuser:1000)
- Expected sizes: frontend-api ~15-20MB, payment-svc ~12-15MB

### Kubernetes Configuration
- ClusterIP services for internal communication
- Health probes configured:
  - Liveness: 10s initial delay, 30s period
  - Readiness: 5s initial delay, 10s period
- Resource requests = limits (guaranteed QoS)
- Total memory footprint: 120Mi (frontend 80Mi + payment 40Mi)
- Total CPU: 80m (frontend 50m + payment 30m)

### Key Implementation Details

**Logging Middleware**:
- Captures method, path, source IP for every request
- Measures request latency
- Sends metrics asynchronously (non-blocking)
- Panic recovery in metrics goroutine

**Decoy Latency**:
- Applied via time.Sleep() in middleware
- Only active when IS_DECOY=true and DECOY_TYPE=slow
- Configurable per-request delay

**Metrics Reporting**:
- Fire-and-forget POST to reporter-service/api/ingest
- 2-second timeout on HTTP client
- Includes: timestamp, service, method, path, source_ip, status_code, latency_ms
- Graceful failure (no crash if reporter unavailable)

### Testing Commands

**Local Testing**:
```bash
# Normal mode
cd services/frontend-api && go run cmd/main.go

# Slow decoy (500ms latency)
IS_DECOY=true DECOY_TYPE=slow DECOY_LATENCY=500 go run cmd/main.go

# Logger decoy
IS_DECOY=true DECOY_TYPE=logger DECOY_LOGGING=verbose go run cmd/main.go
```

**Docker Build**:
```bash
cd services/frontend-api && docker build -t frontend-api:latest .
cd services/payment-svc && docker build -t payment-svc:latest .
```

**K8s Deployment**:
```bash
kubectl apply -f deploy/k8s/frontend-api.yaml
kubectl apply -f deploy/k8s/payment-svc.yaml
kubectl port-forward svc/frontend-api 8080:8080
```

### Memory & Resource Compliance
- frontend-api: 80Mi limit (well under 100Mi target)
- payment-svc: 40Mi limit (well under 100Mi target)
- Total: 120Mi for both services
- Combined with k3s (<800Mi), total system usage ~920Mi (within 2.5GB budget)

---

## Phase 3: Manager Service (Reverse Proxy)
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Implemented a Go-based reverse proxy manager service that routes traffic to either legitimate services or decoy services based on IP blocking rules. The manager uses in-memory storage for blocked IPs and implements round-robin routing across exactly 3 decoy URLs per blocked IP. Exposed via NodePort 30000 for external access.

### Files Created

#### services/manager/
- `go.mod` - Go module definition
- `cmd/main.go` - Manager service implementation (270 lines)
  - Reverse proxy with IP-based routing
  - In-memory IP blocking with round-robin decoy selection
  - Management API endpoints
  - Structured JSON logging
- `Dockerfile` - Multi-stage Alpine build (CGO disabled, ~12-15MB image)
- `USAGE.md` - Comprehensive usage guide with curl examples

#### deploy/k8s/
- `manager.yaml` - Kubernetes Deployment + Service
  - NodePort 30000 for external access
  - Resource limits: 60Mi RAM / 50m CPU

### Core Functionality

**Reverse Proxy Behavior**:
1. **Normal IPs**: Forward all requests to legitimate frontend-api service
2. **Blocked IPs**: Route to decoy URLs in round-robin fashion (exactly 3 URLs)
3. **No K8s Services**: Direct URL-based routing, no service discovery for decoys

**In-Memory IP Manager**:
- Thread-safe map storing blocked IPs and their decoy URLs
- Round-robin counter per IP (increments with each request)
- No persistence (state lost on restart by design)

### API Endpoints

**Management Endpoints**:
1. `POST /api/block_ip` - Add IP to blocklist with 3 decoy URLs
   - Request: `{"source_ip": "IP", "decoy_urls": ["url1", "url2", "url3"]}`
   - Response: Success confirmation with blocked IP details

2. `POST /api/cleanup` - Remove IP from blocklist
   - Request: `{"source_ip": "IP"}`
   - Response: Success/failure with removal status

3. `GET /health` - Health check with statistics
   - Returns: Status, service name, blocked IP count

4. `GET /api/stats` - Statistics endpoint
   - Returns: Total blocked IPs, list of blocked IPs

**Reverse Proxy**:
- All other requests (`/*`) are proxied based on source IP
- Legitimate traffic → `http://frontend-api:8080`
- Blocked traffic → Round-robin to decoy URLs

### Round-Robin Implementation

```
IP blocked with URLs: [decoy1, decoy2, decoy3]

Request 1 → decoy1 (counter: 1)
Request 2 → decoy2 (counter: 2)
Request 3 → decoy3 (counter: 3)
Request 4 → decoy1 (counter: 4, cycles back)
```

Algorithm: `selectedURL = decoyURLs[counter % len(decoyURLs)]`

### Source IP Detection

Priority order:
1. `X-Forwarded-For` header (for proxied requests)
2. `X-Real-IP` header (alternative proxy header)
3. `r.RemoteAddr` (direct connection fallback)

### Structured Logging

All routing decisions logged in JSON format:

**Block IP Event**:
```json
{
  "timestamp": "2026-02-04T15:30:00Z",
  "action": "block_ip",
  "source_ip": "192.168.1.100",
  "decoy_urls": ["http://d1:8080", "http://d2:8080", "http://d3:8080"]
}
```

**Route to Decoy Event**:
```json
{
  "timestamp": "2026-02-04T15:31:00Z",
  "action": "route_to_decoy",
  "source_ip": "192.168.1.100",
  "selected_url": "http://d1:8080",
  "round_robin_count": 1
}
```

**Route to Legitimate Event**:
```json
{
  "timestamp": "2026-02-04T15:32:00Z",
  "action": "route_to_legitimate",
  "source_ip": "192.168.1.200",
  "method": "GET",
  "path": "/api/products"
}
```

**Cleanup Event**:
```json
{
  "timestamp": "2026-02-04T15:33:00Z",
  "action": "cleanup_ip",
  "source_ip": "192.168.1.100"
}
```

### Configuration

**Environment Variables**:
- `PORT` - Manager service port (default: 8080)
- `LEGITIMATE_SERVICE_URL` - URL of real service (default: http://frontend-api:8080)

**Kubernetes Deployment**:
- Service type: NodePort
- NodePort: 30000 (external access)
- Internal port: 8080
- Resource limits: 60Mi memory, 50m CPU
- Health probes on `/health`

### Usage Examples

**Block an IP**:
```bash
curl -X POST http://localhost:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100",
    "decoy_urls": [
      "http://decoy-frontend-1:8080",
      "http://decoy-frontend-2:8080",
      "http://decoy-frontend-3:8080"
    ]
  }'
```

**Cleanup an IP**:
```bash
curl -X POST http://localhost:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100"
  }'
```

**Check Health**:
```bash
curl http://localhost:30000/health
```

**Get Statistics**:
```bash
curl http://localhost:30000/api/stats
```

### Docker Build

**Binary Size** (stripped): 6.2MB
**Expected Image Size**: ~11-15MB (Alpine base + binary)

**Build Commands**:
```bash
cd services/manager
docker build -t manager:latest .

# For k3s
sudo nerdctl -n k8s.io build -t manager:latest .
```

### Kubernetes Deployment

```bash
# Deploy
kubectl apply -f deploy/k8s/manager.yaml

# Verify
kubectl get pods -l app=manager
kubectl get svc manager

# Access via NodePort
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
curl http://$NODE_IP:30000/health
```

### Architecture Integration

The manager acts as the entry point for all traffic:

```
External Traffic (Port 30000)
         ↓
    Manager Service
         ↓
    ┌────┴────┐
    ↓         ↓
Legitimate  Decoy Services
Service     (Round-Robin)
```

**Integration Points**:
- Sentinel (Phase 4+) will call `/api/block_ip` to block suspicious IPs
- Controller (Phase 5+) will orchestrate decoy deployments
- Reporter (Phase 4+) will receive metrics from decoy services

### Memory Budget Compliance

**Manager Service**: 60Mi (within limits)
**Updated Total**:
- k3s: ~800Mi
- frontend-api: 80Mi
- payment-svc: 40Mi
- manager: 60Mi
- **Total: ~980Mi** (within 2.5GB budget)

### Key Implementation Details

**Thread Safety**:
- Uses `sync.RWMutex` for concurrent access to blocked IPs map
- Read lock for checking if IP is blocked
- Write lock for adding/removing IPs

**Reverse Proxy**:
- Uses Go's `httputil.ReverseProxy` for efficient proxying
- Preserves headers and request body
- Sets `X-Decoy-Routed: true` header for decoy requests
- Maintains `X-Forwarded-For` for IP tracking

**Error Handling**:
- Invalid decoy URLs logged and return 500 error
- Malformed JSON in API requests return 400 error
- Method validation on POST endpoints

**No Persistence**:
- All state in-memory only
- Pod restart clears all blocked IPs
- Designed for ephemeral state managed by Sentinel

### Testing Workflow

1. **Deploy all services** (frontend-api, payment-svc, manager)
2. **Normal request** through manager → routes to frontend-api
3. **Block IP** via `/api/block_ip` with 3 decoy URLs
4. **Subsequent requests** from blocked IP → round-robin to decoys
5. **Cleanup** via `/api/cleanup` → routes back to legitimate service

### Production Considerations

- Round-robin counter uses int (overflows after ~2 billion requests)
- No rate limiting on management endpoints (add in production)
- No authentication on `/api/block_ip` and `/api/cleanup` (secure in Phase 4+)
- In-memory storage suitable for ~1000s of blocked IPs
- Memory per blocked IP: ~200 bytes (negligible)

---
