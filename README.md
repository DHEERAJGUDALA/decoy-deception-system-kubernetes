# Decoy Deception System

A Kubernetes-based deception system that automatically detects attacks and routes attackers to isolated decoy environments while keeping legitimate users on production services.

## Overview

The Decoy Deception System uses behavioral analysis and attack pattern detection to identify malicious traffic. When an attack is detected, the system automatically:
1. Creates isolated decoy pods that mimic production services
2. Blocks the attacker's IP at the reverse proxy layer
3. Routes all subsequent attacker traffic to decoys in round-robin fashion
4. Collects detailed metrics about attacker behavior
5. Auto-cleans decoys after a configurable timeout

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          External Traffic                            │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │   Manager (NodePort)    │
                    │   Reverse Proxy         │
                    │   IP Blocking           │
                    └────┬───────────────┬────┘
                         │               │
              Legitimate │               │ Blocked IPs
                         │               │
                    ┌────▼─────┐    ┌────▼──────────────┐
                    │Frontend  │    │  Decoy Pods (3)   │
                    │  API     │    │  - exact          │
                    │          │    │  - slow (1000ms)  │
                    └────┬─────┘    │  - logger         │
                         │          └───────────────────┘
                    ┌────▼─────┐
                    │Payment   │
                    │Service   │
                    └──────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        Detection & Response                          │
├─────────────────────────────────────────────────────────────────────┤
│  ┌──────────┐      ┌────────────┐      ┌──────────┐               │
│  │ Sentinel │─────▶│ Controller │─────▶│ Manager  │               │
│  │ (Logs)   │      │ (AppGraph) │      │ (Block)  │               │
│  └──────────┘      └────────────┘      └──────────┘               │
│                           │                                         │
│                    ┌──────▼────────┐                               │
│                    │   Dashboard   │                               │
│                    │  (NodePort)   │                               │
│                    └───────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        Metrics Collection                            │
├─────────────────────────────────────────────────────────────────────┤
│  All Services ──────▶ Reporter (Push-based)                         │
│                       - 30min rolling history                        │
│                       - Aggregated stats                             │
│                       - Per-service breakdown                        │
└─────────────────────────────────────────────────────────────────────┘
```

## Components

### Core Services

**Manager** (NodePort 30000)
- Reverse proxy entry point for all traffic
- IP-based routing (legitimate vs blocked)
- Round-robin load balancing to 3 decoys per blocked IP
- In-memory IP blocking state

**Frontend-API** (Port 8080)
- Legitimate e-commerce API
- Endpoints: products, cart, login, checkout
- JSON structured logging
- Async metrics reporting

**Payment-Service** (Port 8081)
- Payment processing service
- Transaction ID generation
- Called by frontend-api

### Detection & Response

**Sentinel** (Attack Detection)
- Monitors pod logs via Kubernetes SharedInformer
- Detects 4 attack types:
  - SQL Injection (critical severity)
  - Path Traversal (high severity)
  - Rate Limiting >50 req/min (medium severity)
  - Auth Brute Force >3 failures/min (high severity)
- 5-minute alert cooldown per IP
- Sends alerts to Controller

**Controller** (Orchestration)
- Kubernetes operator with AppGraph CRD
- Creates 3 decoy pods on alert:
  - **exact**: Identical to production
  - **slow**: 1000ms artificial latency
  - **logger**: Verbose request/response logging
- NetworkPolicy isolation per decoy
- Auto-cleanup after 15 minutes
- Real-time dashboard on NodePort 30090

**Reporter** (Metrics)
- Push-based metrics collection
- Rolling 30-minute history
- Aggregated statistics
- Per-service breakdown
- Lightweight client library

### Infrastructure

**AppGraph CRD**
- Custom Resource for decoy orchestration
- Spec: decoyCount, autoCleanupMinutes
- Status: decoyStatus, cleanupScheduledAt

**NetworkPolicies**
- Ingress: Allow from Manager only
- Egress: Allow to Reporter and DNS
- Blocks all other traffic

## Quick Start

### Prerequisites

- WSL (Windows Subsystem for Linux)
- Go 1.21+
- Docker
- 2.5GB RAM minimum

### 1. Install k3s

```bash
make setup    # Install k3s (~2 min)
make verify   # Verify installation
```

Expected output:
```
✓ k3s is running
Node: ready
k3s memory usage: ~800MB
```

### 2. Build and Deploy

```bash
make build    # Build Docker images (~2-3 min)
make deploy   # Deploy to k3s (~2-3 min)
```

Expected output:
```
========================================
       Deployment Complete! ✓
========================================

Service Endpoints:
  Manager (Entry Point):  http://172.20.0.2:30000
  Controller Dashboard:   http://172.20.0.2:30090
```

### 3. Verify Deployment

```bash
kubectl get pods
```

Expected output (all Running):
```
NAME                           READY   STATUS    RESTARTS   AGE
reporter-xxx                   1/1     Running   0          1m
payment-svc-xxx                1/1     Running   0          1m
frontend-api-xxx               1/1     Running   0          1m
manager-xxx                    1/1     Running   0          1m
sentinel-xxx                   1/1     Running   0          1m
controller-xxx                 1/1     Running   0          1m
```

### 4. Open Dashboard

```bash
make dashboard
```

The dashboard will open at `http://NODE_IP:30090` showing:
- Real-time topology graph
- Metrics panel
- Event timeline

## Demo: Attack Detection

### Step 1: Baseline Normal Traffic

```bash
make test-normal
```

**Expected Behavior:**
- 20 requests over 60 seconds
- All HTTP 200 responses
- No alerts from Sentinel
- Traffic routed to legitimate frontend-api

**Verify Metrics:**
```bash
kubectl port-forward svc/reporter 8080:8080 &
curl http://localhost:8080/api/stats | jq
```

### Step 2: SQL Injection Attack

```bash
make test-sqli
```

**Expected Behavior:**
1. 30 SQL injection attempts sent (~10 seconds)
2. Sentinel detects SQLi patterns within 2-3 seconds
3. Alert sent to Controller with attacker IP
4. Controller creates AppGraph CR
5. 3 decoy pods deployed (<2 seconds with stagger)
6. Manager blocks attacker IP
7. Subsequent traffic routed to decoys

**Verify Detection:**
```bash
# Watch Sentinel logs
make logs

# Expected log output:
{
  "timestamp": "2026-02-04T18:00:00Z",
  "action": "attack_detected",
  "attack_type": "sql_injection",
  "source_ip": "172.20.0.1",
  "severity": "critical"
}
```

**Verify Decoys Created:**
```bash
kubectl get pods -l decoy=true
```

Expected output:
```
NAME                      READY   STATUS    RESTARTS   AGE
decoy-frontend-1-xxx      1/1     Running   0          10s
decoy-frontend-2-xxx      1/1     Running   0          10s
decoy-frontend-3-xxx      1/1     Running   0          10s
```

**Verify AppGraph:**
```bash
kubectl get appgraph
```

Expected output:
```
NAME         AGE
decoy-app    15s
```

**View Dashboard:**
```bash
make dashboard
```

Expected visualization:
- Manager node (blue) in center
- 3 Decoy nodes (orange) connected to Manager
- Frontend-API node (green) connected to Manager
- Event timeline showing "AppGraph Created", "Pod Created" events

### Step 3: Rate Limit Attack

```bash
make test-rate
```

**Expected Behavior:**
1. 70 requests in 5 seconds (~1200 req/min)
2. Sentinel detects rate limit exceeded (threshold: 50 req/min)
3. Another AppGraph created (or existing one reused)
4. IP blocked and routed to decoys

**Verify Rate Detection:**
```bash
kubectl logs -l app=sentinel | grep rate_limit_exceeded
```

Expected log:
```json
{
  "attack_type": "rate_limit_exceeded",
  "source_ip": "172.20.0.1",
  "severity": "medium",
  "evidence": "70 requests in 1 minute (threshold: 50)"
}
```

### Step 4: Monitor Metrics

**Reporter Stats:**
```bash
kubectl port-forward svc/reporter 8080:8080 &
curl http://localhost:8080/api/stats | jq
```

Expected output:
```json
{
  "total_requests": 120,
  "requests_by_service": {
    "frontend-api": 20,
    "decoy-frontend-1": 33,
    "decoy-frontend-2": 34,
    "decoy-frontend-3": 33
  },
  "unique_ips": 1,
  "average_latency_ms": 520.5
}
```

**Per-Service Breakdown:**
```bash
curl http://localhost:8080/api/services | jq
```

Expected output:
```json
{
  "frontend-api": {
    "total_requests": 20,
    "avg_latency": 45.2
  },
  "decoy-frontend-1": {
    "total_requests": 33,
    "avg_latency": 48.5
  },
  "decoy-frontend-2": {
    "total_requests": 34,
    "avg_latency": 1050.3
  },
  "decoy-frontend-3": {
    "total_requests": 33,
    "avg_latency": 52.1
  }
}
```

Note: decoy-frontend-2 (slow type) shows ~1000ms higher latency.

### Step 5: Auto-Cleanup

Wait 15 minutes, then verify auto-cleanup:

```bash
kubectl get appgraph
# Expected: No resources found (auto-deleted after 15 min)

kubectl get pods -l decoy=true
# Expected: No resources found (cascade deleted)
```

## Configuration

### Sentinel Detection Thresholds

Edit `deploy/k8s/sentinel-configmap.yaml`:

```yaml
data:
  rate_limit_threshold: "50"        # Requests per minute
  rate_limit_window: "1m"           # Time window
  auth_failure_limit: "3"           # Failed auth attempts
  auth_failure_window: "1m"         # Time window
  cooldown_period: "5m"             # Alert cooldown per IP
```

### Decoy Auto-Cleanup

Edit AppGraph CR or Controller default:

```yaml
spec:
  autoCleanupMinutes: 15  # Default: 15 minutes
```

### Reporter History Window

Edit `deploy/k8s/reporter.yaml`:

```yaml
env:
- name: HISTORY_DURATION
  value: "30m"           # Default: 30 minutes
- name: CLEANUP_INTERVAL
  value: "5m"            # Default: 5 minutes
```

### Decoy Behavior

Decoys are configured via environment variables in Controller code:

- **exact**: Normal behavior, indistinguishable from production
- **slow**: DECOY_LATENCY=1000 (1000ms artificial delay)
- **logger**: DECOY_LOGGING=verbose (detailed request/response logs)

## Troubleshooting

### Issue: Pods Not Starting (ErrImagePull)

**Symptom:**
```bash
kubectl get pods
# STATUS: ErrImagePull or ImagePullBackOff
```

**Cause:** Images not imported into k3s containerd store (WSL requirement)

**Solution:**
```bash
# Verify images in k3s
sudo k3s ctr images ls | grep -E 'frontend-api|manager|sentinel'

# Re-run deployment (includes image import)
make deploy
```

### Issue: Attacks Not Detected

**Symptom:** No alerts after running `make test-sqli`

**Diagnosis:**
```bash
# Check Sentinel is running
kubectl get pods -l app=sentinel

# View Sentinel logs
kubectl logs -l app=sentinel -f

# Verify ConfigMap
kubectl get configmap sentinel-config -o yaml
```

**Common Causes:**
- Sentinel not running
- ConfigMap missing or incorrect
- Services not producing JSON logs
- Source IP extraction failing

**Solution:**
```bash
# Restart Sentinel
kubectl delete pod -l app=sentinel

# Verify log format from frontend-api
kubectl logs -l app=frontend-api | head -5

# Should see JSON logs like:
{"timestamp":"...","method":"GET","path":"/api/products","source_ip":"..."}
```

### Issue: Decoys Not Created

**Symptom:** Alert sent but no decoy pods

**Diagnosis:**
```bash
# Check Controller logs
kubectl logs -l app=controller

# Check AppGraph status
kubectl get appgraph -o yaml

# Check RBAC permissions
kubectl auth can-i create pods --as=system:serviceaccount:default:controller
```

**Common Causes:**
- Controller not running
- RBAC permissions missing
- AppGraph CRD not installed
- Resource limits preventing pod creation

**Solution:**
```bash
# Verify Controller RBAC
kubectl get clusterrole controller-role -o yaml

# Verify AppGraph CRD
kubectl get crd appgraphs.deception.k8s.io

# Check Controller events
kubectl describe pod -l app=controller
```

### Issue: Dashboard Not Accessible

**Symptom:** Cannot access http://NODE_IP:30090

**Diagnosis:**
```bash
# Get node IP
kubectl get nodes -o wide

# Check Controller service
kubectl get svc controller

# Check Controller pod
kubectl get pods -l app=controller
kubectl logs -l app=controller
```

**Solution:**
```bash
# Port-forward as fallback
kubectl port-forward svc/controller 8090:8080

# Access at http://localhost:8090

# Or verify NodePort
kubectl get svc controller -o yaml | grep nodePort
# Should show: nodePort: 30090
```

### Issue: High Memory Usage

**Symptom:** k3s or pods using excessive memory

**Diagnosis:**
```bash
# Check overall memory
free -h

# Check k3s memory
ps aux | grep k3s | awk '{sum+=$6} END {print sum/1024 " MB"}'

# Check pod memory
kubectl top pods
```

**Common Causes:**
- Too many decoy AppGraphs active
- Reporter history window too large
- Memory leaks in services

**Solution:**
```bash
# Delete old AppGraphs
kubectl delete appgraph --all

# Reduce Reporter history
# Edit deploy/k8s/reporter.yaml
# HISTORY_DURATION: "15m"  # Instead of 30m

# Restart high-memory pod
kubectl delete pod <pod-name>
```

### Issue: Round-Robin Not Working

**Symptom:** Attacker always routed to same decoy

**Diagnosis:**
```bash
# Check Manager logs
kubectl logs -l app=manager | grep route_to_decoy

# Should see different decoy URLs:
{"action":"route_to_decoy","selected_url":"http://decoy-frontend-1:8080",...}
{"action":"route_to_decoy","selected_url":"http://decoy-frontend-2:8080",...}
{"action":"route_to_decoy","selected_url":"http://decoy-frontend-3:8080",...}
```

**Solution:**
```bash
# Verify 3 decoy URLs in Manager state
kubectl logs -l app=manager | grep block_ip

# Should see:
{"action":"block_ip","decoy_urls":["http://decoy-frontend-1:8080",...]}

# Restart Manager if needed
kubectl delete pod -l app=manager
```

## Performance Benchmarks

Based on testing with k3s on WSL:

| Metric | Value |
|--------|-------|
| **Deployment** | |
| Total deployment time | 4-5 minutes |
| Image build time | 2-3 minutes |
| Pod startup time | 30-60 seconds |
| **Detection** | |
| SQLi detection latency | 2-3 seconds |
| Rate limit detection | 1-2 seconds |
| Alert to decoy deployment | <2 seconds |
| **Resource Usage** | |
| k3s base memory | ~800MB |
| Total system memory | ~1.34GB |
| Total CPU allocation | 380m |
| **Decoy Operations** | |
| Decoy creation time | <2 seconds (3 pods) |
| Auto-cleanup delay | 15 minutes |
| **Metrics** | |
| Metric ingestion latency | <1ms |
| Stats aggregation (10k metrics) | ~5ms |

## Resource Allocation

| Component | Memory | CPU | QoS | Phase |
|-----------|--------|-----|-----|-------|
| k3s | ~800Mi | N/A | - | 1 |
| frontend-api | 80Mi | 50m | Guaranteed | 2 |
| payment-svc | 40Mi | 30m | Guaranteed | 2 |
| manager | 60Mi | 50m | Guaranteed | 3 |
| sentinel | 80Mi | 50m | Guaranteed | 4 |
| controller | 100Mi | 100m | Guaranteed | 5 |
| reporter | 60Mi | 40m | Guaranteed | 6 |
| decoy-1 | 40Mi | 20m | Guaranteed | 5 |
| decoy-2 | 40Mi | 20m | Guaranteed | 5 |
| decoy-3 | 40Mi | 20m | Guaranteed | 5 |
| **Total** | **~1.34GB** | **380m** | - | - |

**Headroom:** 1.16GB remaining (within 2.5GB budget)

## Makefile Reference

### Setup
```bash
make check         # Check dependencies
make setup         # Install k3s
make verify        # Verify installation
make clean         # Uninstall k3s
```

### Deployment
```bash
make build         # Build Docker images
make deploy        # Deploy all services
make clean-deploy  # Remove deployments
make clean-images  # Remove Docker images
```

### Testing
```bash
make test          # Run all tests
make test-normal   # Normal traffic
make test-sqli     # SQL injection attack
make test-rate     # Rate limit attack
```

### Monitoring
```bash
make dashboard     # Open dashboard
make logs          # Tail Sentinel logs
```

## Security Considerations

### Production Deployment

**Authentication:**
- Add authentication to Controller dashboard (OAuth2, JWT)
- Secure Manager API endpoints (/api/block_ip, /api/cleanup)
- Enable mutual TLS between services

**Network Policies:**
- Current NetworkPolicies require CNI plugin support (Calico, Cilium)
- k3s default (flannel) does not support NetworkPolicy
- Recommend Canal (Calico + flannel) for production

**Secrets:**
- Store sensitive config in Kubernetes Secrets
- Use RBAC to restrict secret access
- Rotate credentials regularly

**Resource Limits:**
- Enforce PodSecurityPolicy or PodSecurity admission
- Set ResourceQuotas per namespace
- Monitor resource usage with Prometheus

**Logging:**
- Ship logs to external system (ELK, Splunk)
- Encrypt logs at rest
- Implement log retention policies

### Attack Surface

**Exposed Endpoints:**
- Manager: NodePort 30000 (public-facing)
- Controller Dashboard: NodePort 30090 (should be internal only)

**Mitigations:**
- Use ingress controller with TLS for Manager
- Restrict dashboard to internal network
- Implement rate limiting at ingress level
- Enable Web Application Firewall (WAF)

## License

MIT License - See LICENSE file for details.

## Contributing

Contributions welcome! Please read CONTRIBUTING.md for guidelines.

## Support

- GitHub Issues: https://github.com/DHEERAJGUDALA/decoy-deception-system-kubernetes/issues
- Documentation: See `scripts/README.md` for detailed deployment guide

## Acknowledgments

Built with:
- Kubernetes (k3s)
- Go 1.21
- controller-runtime
- D3.js (dashboard visualization)
- gorilla/websocket
