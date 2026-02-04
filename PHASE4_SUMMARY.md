# Phase 4 Completion Summary

## Sentinel Service - Attack Detection and Alerting

### Overview

The Sentinel service monitors Kubernetes pod logs in real-time to detect attacks and send alerts to the Controller service for automated response. It uses Kubernetes SharedInformer for efficient pod watching and implements pattern-based detection for common attack vectors.

### Key Features

1. **Real-Time Log Monitoring**
   - Kubernetes SharedInformer for pod watching
   - Label-based pod selection (configurable)
   - Streaming log consumption from all matching pods
   - JSON log parsing with fallback regex

2. **Attack Detection**
   - SQL Injection (4 regex patterns)
   - Path Traversal (URL-encoded variants)
   - Rate Limiting (50 req/min threshold)
   - Auth Brute Force (3 failures/min threshold)

3. **Alert Management**
   - HTTP POST to Controller service
   - 5-minute cooldown per attacker IP
   - Structured JSON alert payload
   - Severity classification (critical/high/medium)

4. **Configuration**
   - ConfigMap-based settings
   - No code changes for threshold adjustments
   - Regex patterns externalized
   - Environment variable overrides

### Architecture

```
Pod Logs
    ↓
SharedInformer (Watch Pods)
    ↓
Log Stream (Real-time)
    ↓
Attack Detection (Pattern Matching)
    ↓
Cooldown Check (5min per IP)
    ↓
Alert Controller (HTTP POST)
    ↓
Controller → Manager → Decoys
```

### Files Created

```
services/sentinel/
├── go.mod                      # Go module with k8s client-go
├── go.sum                      # Dependency checksums
├── cmd/
│   └── main.go                 # Sentinel implementation (470 lines)
├── Dockerfile                  # Multi-stage Alpine build
└── USAGE.md                    # Usage guide and examples

deploy/k8s/
├── sentinel-rbac.yaml          # ServiceAccount + Role + RoleBinding
├── sentinel-configmap.yaml     # Detection rules and thresholds
└── sentinel.yaml               # Deployment + Service

Documentation/
└── PHASE4_EXAMPLES.md          # Alert payload examples
```

### Attack Detection Rules

#### 1. SQL Injection (Critical)

**Patterns**:
```regex
(?i)(union\s+select|select\s+.*\s+from|insert\s+into|delete\s+from|drop\s+table)
(?i)(or\s+1\s*=\s*1|'\s*or\s+'1'\s*=\s*'1)
(?i)(exec\s*\(|execute\s+immediate)
(?i)(\-\-|;--|\/\*|\*\/)
```

**Examples**:
- `GET /api/products?id=1' UNION SELECT * FROM users--`
- `POST /api/login {"username":"admin' OR '1'='1"}`

#### 2. Path Traversal (High)

**Pattern**:
```regex
(?i)(\.\.\/|\.\.\\|%2e%2e%2f|%2e%2e\/|\.\.%2f)
```

**Examples**:
- `GET /api/file?path=../../../../etc/passwd`
- `GET /api/download?file=%2e%2e%2fetc%2fpasswd`

#### 3. Rate Limit Exceeded (Medium)

**Threshold**: >50 requests per minute from single IP

**Detection**: Sliding window request counter per IP

#### 4. Auth Failure Brute Force (High)

**Threshold**: >3 authentication failures in 1 minute

**Indicators**: HTTP 401, "unauthorized", "authentication failed", "invalid credentials", "login failed"

### Alert Payload

```json
{
  "timestamp": "2026-02-04T16:30:00Z",
  "attack_type": "sql_injection",
  "source_ip": "192.168.1.100",
  "evidence": "GET /api/products?id=1' UNION SELECT * FROM users--",
  "severity": "critical",
  "pod_name": "frontend-api-7d8f9c6b5-abc12",
  "decoy_urls": [
    "http://decoy-frontend-1:8080",
    "http://decoy-frontend-2:8080",
    "http://decoy-frontend-3:8080"
  ]
}
```

### Cooldown Mechanism

**Problem**: Persistent attackers generate alert spam

**Solution**: 5-minute cooldown per IP after first alert

**Behavior**:
- First attack from IP → Alert sent
- Attacks within 5 minutes → Logged but no alert
- After 5 minutes → Next attack triggers new alert

**Benefits**:
- Reduces Controller load
- Prevents alert fatigue
- Maintains detection logging

### RBAC Configuration

**ServiceAccount**: `sentinel`

**Permissions** (Role: `sentinel-role`):
```yaml
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get", "list"]
```

**Security**: Minimal permissions for pod log access only

### Configuration (ConfigMap)

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
- Detection patterns (SQLi, path traversal regex)
- Rate limit threshold and window
- Auth failure threshold and window
- Cooldown period
- Controller endpoint

### Deployment

#### Build Docker Image

```bash
cd services/sentinel
docker build -t sentinel:latest .

# For k3s
sudo nerdctl -n k8s.io build -t sentinel:latest .
```

**Image Size**: ~40-45MB (Alpine + 37MB binary + ca-certs)

#### Deploy to Kubernetes

```bash
# Apply RBAC
kubectl apply -f deploy/k8s/sentinel-rbac.yaml

# Apply ConfigMap
kubectl apply -f deploy/k8s/sentinel-configmap.yaml

# Deploy Sentinel
kubectl apply -f deploy/k8s/sentinel.yaml

# Verify
kubectl get pods -l app=sentinel
kubectl logs -f -l app=sentinel
```

### Testing

#### Test SQLi Detection

```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

curl "http://$NODE_IP:30000/api/products?id=1'%20UNION%20SELECT%20*%20FROM%20users--"

kubectl logs -l app=sentinel | grep sql_injection
```

**Expected Output**:
```
[ALERT] Sending: {"timestamp":"...","attack_type":"sql_injection",...}
[ALERT] Sent successfully to controller
```

#### Test Rate Limiting

```bash
for i in {1..60}; do
  curl -s -H "X-Forwarded-For: 192.168.1.100" \
    "http://$NODE_IP:30000/api/products" > /dev/null &
done
wait

kubectl logs -l app=sentinel | grep rate_limit_exceeded
```

#### Test Path Traversal

```bash
curl "http://$NODE_IP:30000/api/file?path=..%2f..%2fetc%2fpasswd"

kubectl logs -l app=sentinel | grep path_traversal
```

### Resource Allocation

**Sentinel**:
- Memory: 80Mi (request = limit)
- CPU: 50m (request = limit)
- QoS: Guaranteed

**Updated System Total**:
| Component | Memory | CPU |
|-----------|--------|-----|
| k3s | ~800Mi | N/A |
| frontend-api | 80Mi | 50m |
| payment-svc | 40Mi | 30m |
| manager | 60Mi | 50m |
| sentinel | 80Mi | 50m |
| **TOTAL** | **~1.06GB** | **180m** |

**Remaining Budget**: ~1.44GB / 2.5GB

### Attacker State Tracking

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

**Features**:
- Per-IP request counting
- Sliding time windows
- Automatic window reset
- Cooldown tracking

### SharedInformer Benefits

1. **Efficiency**: Single API watch connection for all pods
2. **Caching**: Local cache reduces API server load
3. **Resilience**: Automatic reconnection on failures
4. **Scalability**: Handles 10-20 pods efficiently
5. **Event-Driven**: Immediate detection of new pods

### Integration Flow

```
1. Sentinel detects attack in pod logs
2. Sentinel POSTs alert to Controller
3. Controller validates alert
4. Controller calls Manager /api/block_ip with source_ip and decoy_urls
5. Manager redirects attacker to decoys (round-robin)
6. Attacker interacts with decoy environment
7. Reporter (Phase 6) analyzes decoy traffic
```

### Monitoring

**Sentinel Logs** (structured JSON):
```json
{
  "timestamp": "2026-02-04T16:30:00Z",
  "level": "INFO",
  "action": "alert_sent",
  "source_ip": "192.168.1.100",
  "attack_type": "sql_injection",
  "controller_response": "200 OK"
}
```

**Key Metrics**:
- Total alerts sent
- Alerts per attack type
- Cooldown skip rate
- Controller response times

### Production Considerations

**Strengths**:
- ✓ Minimal RBAC permissions
- ✓ ConfigMap-based configuration
- ✓ Cooldown prevents alert spam
- ✓ Non-blocking alert delivery
- ✓ Graceful error handling

**Limitations**:
- Single replica (no HA)
- In-memory state (no persistence)
- Pattern-based detection (potential false positives)
- Requires structured JSON logs

**Future Enhancements**:
- Multi-replica with leader election
- Persistent attack history
- ML-based anomaly detection
- Prometheus metrics endpoint
- Alert aggregation

### Verification

```bash
# Build test
cd services/sentinel
go build cmd/main.go

# Stripped binary size
CGO_ENABLED=0 go build -ldflags="-w -s" -o sentinel cmd/main.go
ls -lh sentinel
# Expected: ~37MB

# Deploy test
kubectl apply -f deploy/k8s/sentinel-rbac.yaml
kubectl apply -f deploy/k8s/sentinel-configmap.yaml
kubectl apply -f deploy/k8s/sentinel.yaml

# Verify running
kubectl get pods -l app=sentinel
kubectl logs -l app=sentinel
```

---

## Phase 4 Status

**Status**: ✓ COMPLETE

**Files Created**: 8
- services/sentinel/go.mod
- services/sentinel/cmd/main.go (470 lines)
- services/sentinel/Dockerfile
- services/sentinel/USAGE.md
- deploy/k8s/sentinel-rbac.yaml
- deploy/k8s/sentinel-configmap.yaml
- deploy/k8s/sentinel.yaml
- PHASE4_EXAMPLES.md

**Binary Size**: 37MB (stripped)
**Image Size**: ~40-45MB
**Memory**: 80Mi
**CPU**: 50m

**All Requirements Met**:
- ✓ Watches pod logs via SharedInformer
- ✓ Detects SQLi, path traversal, rate limit, auth failures
- ✓ Configurable via ConfigMap
- ✓ POSTs alerts to Controller
- ✓ RBAC for pod log access
- ✓ Cooldown prevents spam
- ✓ Dockerfile and K8s manifests
- ✓ Example alert payloads
- ✓ ACTION_LOG.md updated

**Ready for Phase 5**: Controller Service (Alert Processing and Orchestration)

---
