# Sentinel Service Usage Guide

The Sentinel service monitors pod logs for attack patterns and sends alerts to the Controller.

## Overview

- **Purpose**: Real-time attack detection from application logs
- **Method**: Kubernetes SharedInformer for pod log streaming
- **Detection**: SQLi, path traversal, rate limiting, auth failures
- **Alerting**: HTTP POST to Controller service

## Architecture

```
Pod Logs → Sentinel (SharedInformer) → Attack Detection → Alert Controller
```

## Attack Detection Rules

### 1. SQL Injection (SQLi)
**Severity**: Critical

**Patterns Detected**:
- `UNION SELECT`, `SELECT ... FROM`
- `INSERT INTO`, `DELETE FROM`, `DROP TABLE`
- `OR 1=1`, `' OR '1'='1`
- `EXEC(`, `EXECUTE IMMEDIATE`
- SQL comments: `--`, `;--`, `/*`, `*/`

**Example**:
```
GET /api/products?id=1' UNION SELECT * FROM users--
```

### 2. Path Traversal
**Severity**: High

**Patterns Detected**:
- `../`, `..\`
- URL-encoded variants: `%2e%2e%2f`, `%2e%2e/`, `..%2f`

**Example**:
```
GET /api/file?path=../../etc/passwd
```

### 3. Rate Limit Exceeded
**Severity**: Medium

**Threshold**: >50 requests per minute from single IP

**Detection**: Counts requests per IP within sliding 1-minute window

### 4. Auth Failure Brute Force
**Severity**: High

**Threshold**: >3 authentication failures in 1 minute

**Indicators**:
- HTTP 401 status codes
- Log entries containing "unauthorized", "authentication failed", "invalid credentials", "login failed"

## Alert Cooldown

To prevent alert spam, Sentinel enforces a **5-minute cooldown** per attacker IP. Once an alert is sent for a specific IP, no additional alerts for that IP will be sent for 5 minutes, even if attacks continue.

**Cooldown Logic**:
- First attack from IP → Alert sent
- Subsequent attacks within 5 minutes → No alerts (logged only)
- After 5 minutes → Next attack triggers new alert

## Alert Payload Format

Alerts are sent as JSON to the Controller:

```json
{
  "timestamp": "2026-02-04T16:30:00Z",
  "attack_type": "sql_injection",
  "source_ip": "192.168.1.100",
  "evidence": "GET /api/products?id=1' UNION SELECT * FROM users--",
  "severity": "critical",
  "pod_name": "frontend-api-abc123",
  "decoy_urls": [
    "http://decoy-frontend-1:8080",
    "http://decoy-frontend-2:8080",
    "http://decoy-frontend-3:8080"
  ]
}
```

**Fields**:
- `timestamp`: ISO 8601 UTC timestamp
- `attack_type`: One of: `sql_injection`, `path_traversal`, `rate_limit_exceeded`, `auth_failure_brute_force`
- `source_ip`: Attacker's IP address
- `evidence`: Log line or description of attack
- `severity`: `critical`, `high`, `medium`
- `pod_name`: Kubernetes pod that generated the log
- `decoy_urls`: 3 decoy URLs for Manager to route attacker to

## Configuration

Sentinel is configured via ConfigMap (`sentinel-config`):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: sentinel-config
data:
  controller_url: "http://controller:8080/api/alerts"
  namespace: "default"
  watch_labels: "app=frontend-api"
  rate_limit_threshold: "50"
  rate_limit_window: "1m"
  auth_failure_limit: "3"
  auth_failure_window: "1m"
  cooldown_period: "5m"
```

**Configuration Options**:
- `controller_url`: HTTP endpoint for sending alerts
- `namespace`: Kubernetes namespace to watch
- `watch_labels`: Label selector for pods (e.g., "app=frontend-api")
- `rate_limit_threshold`: Max requests per window (default: 50)
- `rate_limit_window`: Time window for rate limiting (default: 1m)
- `auth_failure_limit`: Max auth failures before alert (default: 3)
- `auth_failure_window`: Time window for auth failures (default: 1m)
- `cooldown_period`: Time between alerts for same IP (default: 5m)

## RBAC Requirements

Sentinel requires permissions to:
1. List and watch pods
2. Read pod logs

**ServiceAccount**: `sentinel`

**Permissions**:
```yaml
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["pods/log"]
  verbs: ["get", "list"]
```

## Deployment

### Build Docker Image

```bash
cd services/sentinel
docker build -t sentinel:latest .

# For k3s
sudo nerdctl -n k8s.io build -t sentinel:latest .
```

### Deploy to Kubernetes

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

## Testing

### Simulate SQL Injection Attack

```bash
# Send SQLi request to frontend-api
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

curl "http://$NODE_IP:30000/api/products?id=1'%20UNION%20SELECT%20*%20FROM%20users--"

# Check sentinel logs
kubectl logs -l app=sentinel | grep sql_injection
```

**Expected Output**:
```json
{
  "timestamp": "2026-02-04T16:30:00Z",
  "action": "ALERT",
  "attack_type": "sql_injection",
  "source_ip": "192.168.1.100",
  "evidence": "GET /api/products?id=1' UNION SELECT * FROM users--",
  "severity": "critical"
}
```

### Simulate Path Traversal

```bash
curl "http://$NODE_IP:30000/api/file?path=../../etc/passwd"

kubectl logs -l app=sentinel | grep path_traversal
```

### Simulate Rate Limiting

```bash
# Send 60 requests rapidly
for i in {1..60}; do
  curl -s "http://$NODE_IP:30000/api/products" > /dev/null
done

# Check for rate limit alert
kubectl logs -l app=sentinel | grep rate_limit_exceeded
```

### Simulate Auth Failures

```bash
# Send 5 failed login attempts
for i in {1..5}; do
  curl -X POST "http://$NODE_IP:30000/api/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"wrong"}' > /dev/null
  sleep 5
done

# Check for auth failure alert
kubectl logs -l app=sentinel | grep auth_failure
```

## Log Streaming

Sentinel uses Kubernetes SharedInformer for efficient log streaming:

**Advantages**:
- Single watch connection to Kubernetes API
- Automatic reconnection on failures
- Efficient memory usage
- Scales to multiple pods

**Process**:
1. SharedInformer watches pods with label `app=frontend-api`
2. When pod is detected, Sentinel starts log stream
3. Logs are processed line-by-line in real-time
4. Attack patterns trigger alerts

## Attacker State Tracking

Sentinel maintains in-memory state for each attacker IP:

```go
type AttackerState struct {
    RequestCount   int           // Requests in current window
    AuthFailures   int           // Auth failures in current window
    LastSeen       time.Time     // Last request timestamp
    FirstSeen      time.Time     // Window start time
    LastAlertTime  time.Time     // Last alert sent
    AlertsSent     int           // Total alerts for this IP
}
```

**State Management**:
- Sliding windows for rate limiting and auth failures
- Automatic window reset after timeout
- Cooldown tracking per IP

## Integration with Controller

Sentinel sends alerts to Controller, which:
1. Receives alert via POST `/api/alerts`
2. Validates alert payload
3. Calls Manager's `/api/block_ip` to redirect attacker
4. Logs action for audit

**Alert Flow**:
```
Sentinel → Controller → Manager → Decoy Services
```

## Environment Variables

Override ConfigMap values with environment variables:

```yaml
env:
- name: CONTROLLER_URL
  value: "http://controller:8080/api/alerts"
- name: NAMESPACE
  value: "default"
- name: WATCH_LABELS
  value: "app=frontend-api"
```

## Troubleshooting

### Sentinel not detecting attacks

**Check**:
1. Verify pods are being watched: `kubectl logs -l app=sentinel | grep "Streaming logs"`
2. Ensure frontend-api pods have label `app=frontend-api`
3. Check log format includes JSON with `source_ip` field

### Alerts not reaching Controller

**Check**:
1. Controller service is running: `kubectl get svc controller`
2. Network connectivity: `kubectl exec -it <sentinel-pod> -- wget -O- http://controller:8080/health`
3. Sentinel logs for HTTP errors

### High memory usage

**Adjust**:
- Reduce number of pods watched (change `watch_labels`)
- Increase memory limits in deployment
- Reduce `TailLines` in log streaming (currently 10)

## Resource Usage

**Memory**: 80Mi (limit)
**CPU**: 50m (limit)

**Typical Usage**:
- Idle: ~30Mi, 5m CPU
- Active (10 pods): ~60Mi, 30m CPU

## Production Considerations

1. **False Positives**: Tune regex patterns in ConfigMap to reduce false positives
2. **Alert Volume**: Adjust cooldown period if too many alerts
3. **Scalability**: Single Sentinel instance can watch 10-20 pods efficiently
4. **High Availability**: Run multiple replicas with leader election (future enhancement)
5. **Persistence**: Consider external alert storage for audit trail

---

**Phase 4 Complete** - Sentinel ready for integration with Controller (Phase 5)
