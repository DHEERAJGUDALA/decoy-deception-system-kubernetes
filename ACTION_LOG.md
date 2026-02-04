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

## Phase 4: Sentinel Service (Attack Detection)
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Implemented Sentinel service that monitors pod logs via Kubernetes SharedInformer to detect attacks in real-time. Sentinel identifies SQL injection, path traversal, rate limiting violations, and authentication brute force attempts, then sends alerts to the Controller service for automated response. Includes RBAC configuration for secure pod log access and configurable detection thresholds via ConfigMap.

### Files Created

#### services/sentinel/
- `go.mod` - Go module with Kubernetes client-go dependencies
- `go.sum` - Dependency checksums (auto-generated)
- `cmd/main.go` - Sentinel service implementation (470 lines)
  - SharedInformer for pod watching
  - Real-time log streaming and parsing
  - Attack pattern detection (SQLi, path traversal, rate limit, auth failures)
  - Attacker state tracking with cooldown
  - HTTP alert posting to Controller
- `Dockerfile` - Multi-stage Alpine build (CGO disabled, ~40-45MB image)
- `USAGE.md` - Comprehensive usage guide and examples

#### deploy/k8s/
- `sentinel-rbac.yaml` - ServiceAccount, Role, RoleBinding
  - Permissions: pods (get, list, watch), pods/log (get, list)
- `sentinel-configmap.yaml` - Configuration for detection rules
  - Attack patterns (SQLi, path traversal regex)
  - Thresholds (rate limit: 50 req/min, auth failures: 3/min)
  - Cooldown period: 5 minutes
- `sentinel.yaml` - Deployment + Service
  - Resource limits: 80Mi RAM / 50m CPU
  - ServiceAccount binding for RBAC

#### Documentation
- `PHASE4_EXAMPLES.md` - Alert payload examples for all attack types

### Core Functionality

**Pod Log Monitoring**:
- Uses Kubernetes SharedInformer for efficient pod watching
- Watches pods with label selector (configurable, default: app=frontend-api)
- Streams logs in real-time from all matching pods
- Parses JSON-formatted logs to extract source_ip

**Attack Detection**:

1. **SQL Injection (SQLi)** - Severity: Critical
   - Patterns: UNION SELECT, OR 1=1, INSERT INTO, DROP TABLE, SQL comments
   - Regex-based detection with 4 pattern rules
   - Detects both standard and obfuscated SQLi attempts

2. **Path Traversal** - Severity: High
   - Patterns: ../, ..\, URL-encoded variants (%2e%2e%2f)
   - Single comprehensive regex pattern
   - Catches directory traversal attempts

3. **Rate Limit Exceeded** - Severity: Medium
   - Threshold: >50 requests per minute from single IP
   - Sliding window implementation
   - Automatic window reset after timeout

4. **Auth Failure Brute Force** - Severity: High
   - Threshold: >3 authentication failures in 1 minute
   - Detects: 401 status, "unauthorized", "authentication failed", "login failed"
   - Sliding window with automatic reset

**Attacker State Tracking**:
```go
type AttackerState struct {
    RequestCount   int       // Requests in current window
    AuthFailures   int       // Auth failures in current window
    LastSeen       time.Time // Last request timestamp
    FirstSeen      time.Time // Window start time
    LastAlertTime  time.Time // Last alert sent
    AlertsSent     int       // Total alerts for this IP
}
```

**Alert Cooldown**:
- 5-minute cooldown period per attacker IP
- Prevents alert spam for persistent attackers
- First attack triggers alert, subsequent attacks logged but not alerted
- After cooldown expires, next attack triggers new alert

### Alert Payload Structure

```json
{
  "timestamp": "2026-02-04T16:30:00Z",
  "attack_type": "sql_injection|path_traversal|rate_limit_exceeded|auth_failure_brute_force",
  "source_ip": "192.168.1.100",
  "evidence": "log_line_or_description",
  "severity": "critical|high|medium",
  "pod_name": "frontend-api-7d8f9c6b5-abc12",
  "decoy_urls": [
    "http://decoy-frontend-1:8080",
    "http://decoy-frontend-2:8080",
    "http://decoy-frontend-3:8080"
  ]
}
```

**Alert Fields**:
- `timestamp`: ISO 8601 UTC timestamp
- `attack_type`: Category of detected attack
- `source_ip`: Attacker's IP address extracted from logs
- `evidence`: Original log line or attack summary
- `severity`: Risk level (critical/high/medium)
- `pod_name`: Kubernetes pod that generated the log
- `decoy_urls`: 3 decoy URLs for Manager to route attacker to

### Kubernetes Integration

**RBAC Requirements**:
- ServiceAccount: `sentinel`
- Role: `sentinel-role` (namespace-scoped)
  - Permissions on `pods`: get, list, watch
  - Permissions on `pods/log`: get, list
- RoleBinding: `sentinel-rolebinding`

**SharedInformer Benefits**:
- Single watch connection to Kubernetes API (efficient)
- Automatic caching and synchronization
- Event-driven pod detection (AddFunc, UpdateFunc)
- Built-in reconnection on failures
- Low memory overhead

**Log Streaming**:
- Uses `clientset.CoreV1().Pods().GetLogs()` with streaming
- `Follow: true` for real-time log tailing
- `TailLines: 10` to catch recent logs on startup
- Buffered reading (2000 bytes) for efficiency
- Line-by-line processing with trimming

### Configuration via ConfigMap

All detection parameters configurable without code changes:

```yaml
controller_url: "http://controller:8080/api/alerts"
namespace: "default"
watch_labels: "app=frontend-api"
rate_limit_threshold: "50"
rate_limit_window: "1m"
auth_failure_limit: "3"
auth_failure_window: "1m"
cooldown_period: "5m"
```

**Tunable Parameters**:
- Attack pattern regexes (SQLi, path traversal)
- Rate limit threshold and window
- Auth failure threshold and window
- Alert cooldown period
- Decoy URLs for alerts

### Source IP Extraction

Two-tier extraction strategy:

1. **JSON Parsing** (preferred):
   - Parse log as JSON
   - Extract `source_ip` field directly
   - Works with structured logs from frontend-api/manager

2. **Regex Fallback**:
   - Pattern: `\b(?:\d{1,3}\.){3}\d{1,3}\b`
   - Finds first IP address in log line
   - Handles unstructured logs

### Alert Delivery

**HTTP POST to Controller**:
- Endpoint: `http://controller:8080/api/alerts`
- Content-Type: application/json
- Timeout: 5 seconds
- Async fire-and-forget (non-blocking)
- Error logging for failed deliveries

**Success Criteria**:
- HTTP 200 OK or 201 Created from Controller
- Alert marked as sent in attacker state
- LastAlertTime updated for cooldown

### Docker Build

**Binary Size** (stripped): 37MB
**Expected Image Size**: ~40-45MB (Alpine base + binary + ca-certificates)

**Build Process**:
- Builder stage: golang:1.21-alpine
- Dependencies: k8s.io/client-go v0.28.0
- CGO disabled for static binary
- Binary stripping: -ldflags="-w -s"
- Final stage: alpine:latest with ca-certificates
- Non-root user: appuser:1000

### Resource Allocation

**Sentinel Service**:
- Memory: 80Mi (request = limit)
- CPU: 50m (request = limit)
- QoS: Guaranteed

**Updated System Total**:
| Component | Memory | CPU | Phase |
|-----------|--------|-----|-------|
| k3s | ~800Mi | N/A | 1 |
| frontend-api | 80Mi | 50m | 2 |
| payment-svc | 40Mi | 30m | 2 |
| manager | 60Mi | 50m | 3 |
| sentinel | 80Mi | 50m | 4 |
| **TOTAL** | **~1.06GB** | **180m** | **Within 2.5GB budget ✓** |

### Detection Logic Flow

```
1. Pod logs → SharedInformer watches pods
2. Log stream → Real-time log reading
3. Parse log → Extract source_ip and content
4. Check patterns → SQLi, path traversal, auth failure
5. Check rate → Count requests per IP per window
6. Detect attack → Pattern match or threshold exceeded
7. Check cooldown → Ensure 5min since last alert
8. Send alert → POST to Controller
9. Update state → Record alert time, increment counter
```

### Example Attack Scenarios

**SQL Injection**:
```
Request: GET /api/products?id=1' UNION SELECT * FROM users--
Detection: SQLi pattern match
Alert: attack_type="sql_injection", severity="critical"
```

**Path Traversal**:
```
Request: GET /api/file?path=../../../../etc/passwd
Detection: Path traversal pattern match
Alert: attack_type="path_traversal", severity="high"
```

**Rate Limiting**:
```
Scenario: 60 requests in 1 minute from 192.168.1.100
Detection: Request count > 50 threshold
Alert: attack_type="rate_limit_exceeded", severity="medium"
```

**Auth Brute Force**:
```
Scenario: 5 failed login attempts in 1 minute
Detection: Auth failure count > 3 threshold
Alert: attack_type="auth_failure_brute_force", severity="high"
```

### Integration Flow

```
Sentinel → Controller → Manager → Decoy Services

1. Sentinel detects attack in logs
2. Sentinel sends alert to Controller
3. Controller calls Manager's /api/block_ip
4. Manager routes attacker to decoys (round-robin)
5. Attacker interacts with decoy environment
```

### Testing Commands

**Deploy Sentinel**:
```bash
kubectl apply -f deploy/k8s/sentinel-rbac.yaml
kubectl apply -f deploy/k8s/sentinel-configmap.yaml
kubectl apply -f deploy/k8s/sentinel.yaml
```

**Test SQLi Detection**:
```bash
curl "http://NODE_IP:30000/api/products?id=1'%20UNION%20SELECT%20*%20FROM%20users--"
kubectl logs -l app=sentinel | grep sql_injection
```

**Test Rate Limiting**:
```bash
for i in {1..60}; do curl -s http://NODE_IP:30000/api/products > /dev/null & done
kubectl logs -l app=sentinel | grep rate_limit_exceeded
```

### Monitoring and Observability

**Sentinel Logs** (JSON structured):
- Detection events with attack type and source IP
- Alert sent confirmations with Controller response
- Cooldown skip events with reason
- Error logs for failed alert deliveries

**Key Metrics** (from logs):
- Total alerts sent per IP
- Alert types distribution
- Cooldown effectiveness (skip count)
- Controller response times

### Limitations and Considerations

**In-Memory State**:
- Attacker states cleared on pod restart
- No persistence of historical attacks
- Suitable for transient attack detection

**Pattern-Based Detection**:
- Regex patterns may have false positives
- Tune patterns in ConfigMap based on traffic
- Consider ML-based detection in future

**Single Replica**:
- Current deployment: 1 replica
- No high availability
- Consider leader election for multi-replica (future)

**Log Format Dependency**:
- Requires JSON logs with source_ip field
- Fallback regex for unstructured logs
- Services must emit proper log format

### Production Readiness

**Implemented**:
- ✓ RBAC with minimal required permissions
- ✓ ConfigMap-based configuration
- ✓ Cooldown to prevent alert spam
- ✓ Graceful error handling
- ✓ Non-blocking alert delivery
- ✓ Resource limits

**Future Enhancements**:
- Multi-replica deployment with leader election
- Persistent attack history (database)
- ML-based anomaly detection
- Custom alert destinations (Slack, PagerDuty)
- Metrics endpoint (Prometheus)
- Alert aggregation and batching

---
## Phase 5: AppGraph Controller (CRD-Based Orchestration)
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Implemented a Kubernetes operator using controller-runtime that manages decoy orchestration via Custom Resource Definitions (AppGraph CRD). The Controller automatically creates 3 decoy pods (exact, slow, logger types) with NetworkPolicy isolation and auto-cleanup after 15 minutes. Includes a real-time web dashboard with D3.js force-directed graph visualization and WebSocket updates on NodePort 30090.

### Files Created

#### services/controller/
- `go.mod` - Go module with controller-runtime v0.16.0 and gorilla/websocket v1.5.0
- `go.sum` - Dependency checksums (auto-generated)
- `cmd/main.go` - Controller implementation (650+ lines)
- `Dockerfile` - Multi-stage Alpine build (CGO disabled)
- `USAGE.md` - Dashboard access and AppGraph CR examples

#### deploy/k8s/
- `appgraph-crd.yaml` - Custom Resource Definition
- `controller-rbac.yaml` - ServiceAccount, ClusterRole, ClusterRoleBinding
- `controller.yaml` - Deployment + Service (NodePort 30090)

### Resource Allocation

**Controller**: 100Mi RAM / 100m CPU
**Decoys (3)**: 120Mi RAM / 60m CPU

---

## Phase 6: Reporter Service (Metrics Collection)
**Status**: ✓ Completed
**Date**: 2026-02-04

### Summary
Implemented a lightweight push-based metrics collection service with rolling 30-minute history and automated cleanup. Reporter aggregates metrics from all decoy and legitimate services via a simple POST /api/ingest endpoint. Includes a client helper library for fire-and-forget async metric sending. Resource-efficient design with 5.7MB binary and 60Mi memory limit.

### Files Created

#### services/reporter/
- `go.mod` - Go module definition (Go 1.21, no external dependencies)
- `cmd/main.go` - Reporter service implementation (342 lines)
  - POST /api/ingest for metric ingestion
  - GET /api/stats for aggregated statistics
  - GET /api/services for per-service breakdown
  - GET /health for health check
  - Rolling 30-minute metric history
  - Automated cleanup worker (5-minute interval)
- `client/client.go` - Lightweight client helper library
  - NewClient(url) constructor
  - Send(metric) - Fire-and-forget async
  - SendSync(metric) - Blocking send with error
- `client/go.mod` - Client module definition
- `Dockerfile` - Multi-stage Alpine build (5.7MB binary, ~10-12MB image)

#### deploy/k8s/
- `reporter.yaml` - Deployment + Service
  - ClusterIP service (internal only)
  - Resource limits: 60Mi RAM / 40m CPU
  - Environment variables: PORT, HISTORY_DURATION, CLEANUP_INTERVAL

### Core Functionality

**Metric Ingestion**:
- Endpoint: `POST /api/ingest`
- Thread-safe in-memory storage
- Automatic timestamp addition
- JSON structured metrics

**Metric Structure**:
```go
type Metric struct {
    Timestamp  string
    Service    string
    Method     string
    Path       string
    SourceIP   string
    StatusCode int
    Latency    int64
    Custom     map[string]interface{}
}
```

**Aggregated Statistics** (GET /api/stats):
- Total requests
- Requests by service/IP/path
- Average latency
- Status code distribution
- Unique IP count
- Time range coverage

**Per-Service Breakdown** (GET /api/services):
- Total requests per service
- Unique IPs per service
- Path distribution per service
- Average latency per service

**Rolling History**:
- Default: 30-minute retention window (configurable)
- Automatic cleanup every 5 minutes (configurable)
- Background cleanup worker goroutine
- Removes metrics older than retention window

**Thread Safety**:
- sync.RWMutex for concurrent access
- Read locks for queries
- Write locks for ingestion/cleanup

### Client Helper Library

**Usage Example**:
```go
import "github.com/decoy-deception-system/reporter/client"

client := client.NewClient("http://reporter:8080/api/ingest")

// Fire-and-forget (async)
client.Send(client.Metric{
    Service:    "frontend-api",
    Method:     "GET",
    Path:       "/api/products",
    SourceIP:   "192.168.1.100",
    StatusCode: 200,
    Latency:    45,
})

// Blocking with error handling
err := client.SendSync(metric)
```

**Client Features**:
- Minimal dependencies (stdlib only)
- 2-second timeout for reliability
- Async Send() for non-blocking
- Sync SendSync() for error handling
- Automatic timestamp generation

### Configuration

**Environment Variables**:
- `PORT` - HTTP server port (default: 8080)
- `HISTORY_DURATION` - Metric retention (default: 30m)
- `CLEANUP_INTERVAL` - Cleanup frequency (default: 5m)

### Docker Build

**Binary Size**: 5.7MB (stripped)
**Image Size**: ~10-12MB (Alpine + binary + ca-certificates)

**Build Commands**:
```bash
cd services/reporter
docker build -t reporter:latest .

# For k3s
sudo nerdctl -n k8s.io build -t reporter:latest .
```

### Kubernetes Deployment

```bash
kubectl apply -f deploy/k8s/reporter.yaml
kubectl get pods -l app=reporter
kubectl get svc reporter
```

### Resource Allocation

**Reporter Service**:
- Memory: 60Mi (request = limit)
- CPU: 40m (request = limit)
- QoS: Guaranteed

**Updated System Total**:
| Component | Memory | CPU | Phase |
|-----------|--------|-----|-------|
| k3s | ~800Mi | N/A | 1 |
| frontend-api | 80Mi | 50m | 2 |
| payment-svc | 40Mi | 30m | 2 |
| manager | 60Mi | 50m | 3 |
| sentinel | 80Mi | 50m | 4 |
| controller | 100Mi | 100m | 5 |
| reporter | 60Mi | 40m | 6 |
| decoys (3) | 120Mi | 60m | 5 |
| **TOTAL** | **~1.34GB** | **380m** | **Within 2.5GB budget ✓** |

### Performance Characteristics

**Memory Usage**:
- ~200 bytes per metric (estimated)
- 30min @ 10 req/sec = 18,000 metrics = ~3.6MB
- Well within 60Mi memory limit

**Latency**:
- Ingestion: <1ms (in-memory append)
- Stats aggregation: O(n) linear scan
- Service breakdown: O(n) linear scan
- Cleanup: O(n) linear scan

**Concurrency**:
- Unlimited concurrent ingestion
- Unlimited concurrent queries (RWMutex multiple readers)
- Single cleanup worker

### API Examples

**Ingest Metric**:
```bash
curl -X POST http://reporter:8080/api/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "service": "frontend-api",
    "method": "GET",
    "path": "/api/products",
    "source_ip": "192.168.1.100",
    "status_code": 200,
    "latency_ms": 45
  }'
```

**Get Stats**:
```bash
curl http://reporter:8080/api/stats
```

**Get Service Breakdown**:
```bash
curl http://reporter:8080/api/services
```

**Health Check**:
```bash
curl http://reporter:8080/health
```

### Integration with Other Services

**Frontend-API Integration**:
```go
go func() {
    defer func() { recover() }()
    client := reporter.NewClient("http://reporter:8080/api/ingest")
    client.Send(reporter.Metric{
        Service:    "frontend-api",
        Method:     r.Method,
        Path:       r.URL.Path,
        SourceIP:   sourceIP,
        StatusCode: 200,
        Latency:    latency,
    })
}()
```

**Decoy Integration**:
- Same client usage as frontend-api
- Service name: "decoy-frontend-1", "decoy-frontend-2", "decoy-frontend-3"
- REPORTER_URL env var: "http://reporter:8080/api/ingest"

### Monitoring and Observability

**Reporter Logs**:
- Metric ingestion: `[INGEST] frontend-api from 192.168.1.100 - GET /api/products (status: 200, latency: 45ms)`
- Cleanup: `[CLEANUP] Removed 50 old metrics, retained 100`
- Startup: `[CONFIG] Port: 8080`, `[CONFIG] History Duration: 30m0s`

**Health Endpoint Metrics**:
- Current metric count
- History duration setting
- Service status

### Limitations and Considerations

**In-Memory Storage**:
- No persistence (metrics lost on restart)
- Limited by memory allocation (60Mi)
- Suitable for short-term rolling window

**Cleanup Precision**:
- Cleanup runs every 5 minutes (not continuous)
- Metrics may exceed 30min window by up to 5min

**No Authentication**:
- Ingestion endpoint open to all pods
- Suitable for cluster-internal use only

**No Rate Limiting**:
- Unlimited ingestion rate
- Recommend rate limiting at client side

### Production Readiness

**Implemented**:
- ✓ Thread-safe concurrent access
- ✓ Automated cleanup worker
- ✓ Configurable retention window
- ✓ Health check endpoint
- ✓ Lightweight client library
- ✓ Resource limits
- ✓ Graceful error handling

**Future Enhancements**:
- Persistent storage (ClickHouse, InfluxDB)
- Prometheus metrics endpoint
- Rate limiting on ingestion
- Metric sampling for high volume
- Multi-replica deployment
- Dashboard integration (WebSocket)
- Alerting on anomalies

---
