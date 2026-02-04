# Phase 3 Completion Summary

## Manager Service - Reverse Proxy with IP Blocking

### Overview

The manager service is a lightweight Go reverse proxy that intelligently routes traffic based on IP blocking rules. It serves as the entry point for all external traffic (NodePort 30000) and decides whether to route requests to legitimate services or decoy services.

### Key Features

1. **Reverse Proxy**
   - Default: Routes to legitimate frontend-api service
   - Blocked IPs: Routes to decoy URLs in round-robin fashion
   - No Kubernetes Services used for decoy routing (direct URL-based)

2. **IP Management API**
   - `POST /api/block_ip` - Block IP with 3 decoy URLs
   - `POST /api/cleanup` - Unblock IP
   - `GET /health` - Health check with statistics
   - `GET /api/stats` - View blocked IPs

3. **Round-Robin Routing**
   - Exactly 3 decoy URLs per blocked IP
   - Counter-based round-robin (0→1→2→0→...)
   - Equal distribution across all decoys

4. **In-Memory Storage**
   - No database dependencies
   - Thread-safe concurrent access (sync.RWMutex)
   - State cleared on pod restart (by design)

### Architecture

```
External Client
      ↓
Port 30000 (NodePort)
      ↓
  Manager Service
      ↓
   ┌──┴──┐
   ↓     ↓
Legit  Decoys
8080   (RR)
```

### Files Created

```
services/manager/
├── go.mod                    # Go module
├── cmd/
│   └── main.go              # Service implementation (270 lines)
├── Dockerfile               # Multi-stage Alpine build
└── USAGE.md                 # Usage guide with examples

deploy/k8s/
└── manager.yaml             # Deployment + NodePort Service
```

### API Examples

#### Block an IP with 3 Decoys

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

**Response**:
```json
{
  "success": true,
  "message": "IP 192.168.1.100 blocked and routed to 3 decoy URLs",
  "source_ip": "192.168.1.100",
  "decoy_urls": [
    "http://decoy-frontend-1:8080",
    "http://decoy-frontend-2:8080",
    "http://decoy-frontend-3:8080"
  ]
}
```

#### Cleanup (Unblock) an IP

```bash
curl -X POST http://localhost:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100"
  }'
```

**Response**:
```json
{
  "success": true,
  "message": "IP 192.168.1.100 cleanup result",
  "source_ip": "192.168.1.100",
  "removed": true
}
```

#### Health Check

```bash
curl http://localhost:30000/health
```

**Response**:
```json
{
  "status": "healthy",
  "service": "manager",
  "stats": {
    "total_blocked_ips": 2,
    "blocked_ips": ["192.168.1.100", "10.0.0.50"]
  }
}
```

#### Get Statistics

```bash
curl http://localhost:30000/api/stats
```

**Response**:
```json
{
  "total_blocked_ips": 2,
  "blocked_ips": ["192.168.1.100", "10.0.0.50"]
}
```

### Round-Robin Logic

When IP `192.168.1.100` is blocked with 3 decoy URLs:

| Request # | Counter | Selected URL | Calculation |
|-----------|---------|--------------|-------------|
| 1 | 0 | decoy_urls[0] | 0 % 3 = 0 |
| 2 | 1 | decoy_urls[1] | 1 % 3 = 1 |
| 3 | 2 | decoy_urls[2] | 2 % 3 = 2 |
| 4 | 3 | decoy_urls[0] | 3 % 3 = 0 |
| 5 | 4 | decoy_urls[1] | 4 % 3 = 1 |

**Implementation**:
```go
selectedURL := blocked.DecoyURLs[blocked.Counter % len(blocked.DecoyURLs)]
blocked.Counter++
```

### Structured Logging

All routing decisions are logged in JSON format for easy parsing:

**Block IP**:
```json
{
  "timestamp": "2026-02-04T15:30:00Z",
  "action": "block_ip",
  "source_ip": "192.168.1.100",
  "decoy_urls": ["http://d1:8080", "http://d2:8080", "http://d3:8080"]
}
```

**Route to Decoy**:
```json
{
  "timestamp": "2026-02-04T15:31:00Z",
  "action": "route_to_decoy",
  "source_ip": "192.168.1.100",
  "selected_url": "http://d1:8080",
  "round_robin_count": 1
}
```

**Route to Legitimate**:
```json
{
  "timestamp": "2026-02-04T15:32:00Z",
  "action": "route_to_legitimate",
  "source_ip": "192.168.1.200",
  "method": "GET",
  "path": "/api/products"
}
```

**Cleanup**:
```json
{
  "timestamp": "2026-02-04T15:33:00Z",
  "action": "cleanup_ip",
  "source_ip": "192.168.1.100"
}
```

### Source IP Detection

The manager extracts the source IP in priority order:

1. **X-Forwarded-For** header (if present) - for proxied traffic
2. **X-Real-IP** header (if present) - alternative proxy header
3. **RemoteAddr** (fallback) - direct connection

This ensures accurate IP detection even behind load balancers or proxies.

### Deployment

#### Build Docker Image

```bash
cd services/manager
docker build -t manager:latest .

# For k3s
sudo nerdctl -n k8s.io build -t manager:latest .
```

**Image Size**: ~11-15MB (Alpine + 6.2MB stripped binary)

#### Deploy to Kubernetes

```bash
kubectl apply -f deploy/k8s/manager.yaml

# Verify deployment
kubectl get pods -l app=manager
kubectl get svc manager

# Expected output:
# NAME      TYPE       CLUSTER-IP     EXTERNAL-IP   PORT(S)          AGE
# manager   NodePort   10.43.xxx.xxx  <none>        8080:30000/TCP   10s
```

#### Access via NodePort

```bash
# Get node IP
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

# Test health
curl http://$NODE_IP:30000/health

# Test proxying (should route to legitimate service)
curl http://$NODE_IP:30000/api/products
```

### Resource Allocation

**Manager Service**:
- Memory: 60Mi (request = limit)
- CPU: 50m (request = limit)
- QoS: Guaranteed

**Updated System Total**:
| Component | Memory | CPU | Notes |
|-----------|--------|-----|-------|
| k3s | ~800Mi | N/A | Phase 1 |
| frontend-api | 80Mi | 50m | Phase 2 |
| payment-svc | 40Mi | 30m | Phase 2 |
| manager | 60Mi | 50m | Phase 3 |
| **TOTAL** | **~980Mi** | **130m** | **Within 2.5GB budget** |

### Testing Workflow

1. **Deploy Services**:
```bash
kubectl apply -f deploy/k8s/frontend-api.yaml
kubectl apply -f deploy/k8s/payment-svc.yaml
kubectl apply -f deploy/k8s/manager.yaml
```

2. **Normal Request** (not blocked):
```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
curl http://$NODE_IP:30000/api/products
# Routes to legitimate frontend-api
```

3. **Block an IP**:
```bash
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100",
    "decoy_urls": [
      "http://decoy-1:8080",
      "http://decoy-2:8080",
      "http://decoy-3:8080"
    ]
  }'
```

4. **Simulate Blocked Traffic**:
```bash
# Set X-Forwarded-For to test IP blocking
curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/api/products
# Routes to decoy-1 (first request)

curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/api/cart
# Routes to decoy-2 (second request)

curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/api/login
# Routes to decoy-3 (third request)

curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/health
# Routes to decoy-1 (cycles back)
```

5. **Check Statistics**:
```bash
curl http://$NODE_IP:30000/api/stats
```

6. **Cleanup IP**:
```bash
curl -X POST http://$NODE_IP:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{"source_ip": "192.168.1.100"}'
```

7. **Verify Cleanup**:
```bash
curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/api/products
# Now routes back to legitimate service
```

### Integration with Future Phases

**Phase 4 - Sentinel Service**:
- Sentinel will detect suspicious behavior
- Calls `/api/block_ip` to redirect attackers to decoys
- Monitors legitimate service metrics

**Phase 5 - Controller**:
- Manages decoy deployments
- Provides decoy URLs to Sentinel
- Sentinel passes URLs to Manager via `/api/block_ip`

**Phase 6 - Reporter**:
- Collects metrics from decoys
- Analyzes attacker behavior
- Can trigger cleanup via `/api/cleanup` if needed

### Configuration

**Environment Variables**:
- `PORT`: Service port (default: 8080)
- `LEGITIMATE_SERVICE_URL`: Target for legitimate traffic (default: http://frontend-api:8080)

**Kubernetes Configuration**:
```yaml
env:
- name: PORT
  value: "8080"
- name: LEGITIMATE_SERVICE_URL
  value: "http://frontend-api:8080"
```

### Memory Usage

**In-Memory Storage**:
- Each blocked IP entry: ~200 bytes (IP string + 3 URLs + metadata)
- 1000 blocked IPs: ~200KB
- 10,000 blocked IPs: ~2MB
- Service overhead: ~60Mi

**Scalability**: Can handle thousands of blocked IPs within 60Mi limit.

### Thread Safety

All IP management operations are thread-safe:
- `sync.RWMutex` protects the blocked IPs map
- Read lock for checking if IP is blocked (allows concurrent reads)
- Write lock for adding/removing IPs (exclusive access)
- Round-robin counter updates under write lock

### Production Considerations

1. **Counter Overflow**: int counter will overflow after ~2 billion requests (negligible for most use cases)
2. **No Persistence**: State lost on pod restart (intended behavior)
3. **No Auth**: Management endpoints unprotected (add in production)
4. **No Rate Limiting**: Consider adding for `/api/block_ip` endpoint
5. **Error Recovery**: Gracefully handles invalid decoy URLs

### Verification

```bash
# Build test
cd services/manager
go build cmd/main.go

# Stripped binary size
CGO_ENABLED=0 go build -ldflags="-w -s" -o manager cmd/main.go
ls -lh manager
# Expected: ~6.2MB

# Run locally
LEGITIMATE_SERVICE_URL=http://localhost:8080 go run cmd/main.go
```

---

## Phase 3 Status

**Status**: ✓ COMPLETE

**Files Created**: 4
- services/manager/go.mod
- services/manager/cmd/main.go (270 lines)
- services/manager/Dockerfile
- services/manager/USAGE.md
- deploy/k8s/manager.yaml

**Binary Size**: 6.2MB (stripped)
**Image Size**: ~11-15MB
**Resource Limits**: 60Mi RAM / 50m CPU
**NodePort**: 30000

**All Requirements Met**:
- ✓ Reverse proxy to legitimate service
- ✓ POST /api/block_ip with decoy_urls array
- ✓ POST /api/cleanup
- ✓ Round-robin across exactly 3 decoy URLs
- ✓ In-memory only (no K8s Services for routing)
- ✓ Structured JSON logging
- ✓ NodePort 30000 exposure
- ✓ Resource limits 60Mi/50m

**Ready for Phase 4**: Sentinel Service (Anomaly Detection)

---
