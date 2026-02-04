# Phase 5 Completion Summary

## AppGraph Controller with Real-Time Dashboard

### Overview

The Controller service orchestrates decoy deployment through a Custom Resource Definition (AppGraph), manages the lifecycle of decoy pods, enforces network isolation, and provides a real-time dashboard with WebSocket updates showing attack visualization and metrics.

### Key Features

1. **AppGraph CRD**
   - Custom Kubernetes resource for decoy deployment
   - Fixed 3 decoys per attack (exact, slow, logger)
   - Auto-cleanup after 15 minutes
   - Status tracking (Pending → Creating → Active → Cleanup)

2. **Controller Logic**
   - Receives alerts from Sentinel via POST /api/alerts
   - Creates 3 decoy pods with 0.5s stagger (total <1.5s)
   - Unique env vars per decoy type
   - Resource limits: 40Mi RAM / 20m CPU per decoy
   - NetworkPolicy isolation for each decoy
   - Calls Manager /api/block_ip after deployment

3. **Real-Time Dashboard (Port 8090)**
   - Single HTML file with embedded CSS/JS
   - D3.js force-directed graph visualization
   - WebSocket client with auto-reconnect
   - Metrics panel (alerts, decoys, blocked IPs, attack types)
   - Event timeline with animations
   - Drag-and-drop interactive nodes

4. **Network Isolation**
   - NetworkPolicies for each decoy pod
   - Ingress: Only from Manager service
   - Egress: Only to Reporter service
   - Prevents decoy-to-decoy and decoy-to-internet communication

### Architecture

```
Sentinel → POST /api/alerts → Controller
                                  ↓
                          Create AppGraph CR
                                  ↓
                    ┌─────────────┴─────────────┐
                    ↓                           ↓
              Reconcile Loop              WebSocket Broadcast
                    ↓                           ↓
        Create 3 Decoy Pods            Dashboard Clients
        (stagger 0.5s each)
                    ↓
        Create NetworkPolicies
                    ↓
        POST Manager /api/block_ip
                    ↓
        Schedule Cleanup (15min)
```

### Files Created

```
services/controller/
├── go.mod                       # Go module with controller-runtime
├── cmd/
│   └── main.go                  # Controller + Dashboard (650 lines)
└── Dockerfile                   # Multi-stage Alpine build

deploy/k8s/
├── appgraph-crd.yaml           # AppGraph CustomResourceDefinition
├── controller-rbac.yaml        # ServiceAccount + ClusterRole + Binding
└── controller.yaml             # Deployment + NodePort Service (30090)
```

### AppGraph CRD Spec

```yaml
apiVersion: deception.k8s.io/v1
kind: AppGraph
metadata:
  name: ag-192-168-1-100-1234567890
spec:
  services:
  - frontend-api
  decoyCount: 3                    # Fixed at 3
  autoCleanupMinutes: 15           # Auto-delete after 15 minutes
  sourceIP: "192.168.1.100"
  attackType: "sql_injection"
  severity: "critical"
status:
  phase: "Active"
  decoyPods:
  - decoy-ag-192-168-1
  - decoy-ag-192-168-2
  - decoy-ag-192-168-3
  decoyURLs:
  - http://decoy-ag-192-168-1:8080
  - http://decoy-ag-192-168-2:8080
  - http://decoy-ag-192-168-3:8080
  createdAt: "2026-02-04T17:00:00Z"
  cleanupScheduledAt: "2026-02-04T17:15:00Z"
  message: "Deployed 3 decoys"
```

### Decoy Creation Process

**Timing**: <1.5 seconds total (0.5s stagger × 3 decoys)

1. **Decoy 1 - Exact** (t=0ms)
   - Type: exact
   - Latency: 0ms
   - Logging: normal
   - Behavior: Identical to legitimate service

2. **Delay 0.5s** (t=500ms)

3. **Decoy 2 - Slow** (t=500ms)
   - Type: slow
   - Latency: 1000ms
   - Logging: normal
   - Behavior: Adds 1s delay to all requests

4. **Delay 0.5s** (t=1000ms)

5. **Decoy 3 - Logger** (t=1000ms)
   - Type: logger
   - Latency: 0ms
   - Logging: verbose
   - Behavior: Detailed logging of requests/responses

**Total Time**: ~1000ms (well under 1.5s requirement)

### Decoy Pod Configuration

```yaml
Pod:
  Name: decoy-{appgraph}-{sourceIP}-{index}
  Labels:
    app: decoy
    appgraph: {name}
    decoy-type: exact|slow|logger
    source-ip: {sourceIP}
    attack-type: {attackType}
  Containers:
    Image: frontend-api:latest
    ImagePullPolicy: IfNotPresent
    Env:
      IS_DECOY: "true"
      DECOY_TYPE: exact|slow|logger
      DECOY_LATENCY: 0|1000|0
      DECOY_LOGGING: normal|normal|verbose
    Resources:
      Requests/Limits:
        Memory: 40Mi
        CPU: 20m
```

### NetworkPolicy Isolation

```yaml
NetworkPolicy:
  Name: decoy-policy-{podName}
  Spec:
    PodSelector:
      matchLabels:
        app: decoy
        appgraph: {name}
    PolicyTypes:
    - Ingress
    - Egress
    Ingress:
    - From:
      - PodSelector:
          matchLabels:
            app: manager
    Egress:
    - To:
      - PodSelector:
          matchLabels:
            app: reporter-service
```

**Security**:
- Decoys can only receive traffic from Manager
- Decoys can only send traffic to Reporter
- No internet egress
- No inter-decoy communication

### Auto-Cleanup Mechanism

**Lifecycle**:
1. AppGraph created with `autoCleanupMinutes: 15`
2. Status updated with `cleanupScheduledAt` = now + 15 minutes
3. Reconcile loop checks cleanup time every minute
4. When cleanup time reached:
   - Delete AppGraph CR
   - Kubernetes garbage collection deletes owned pods
   - NetworkPolicies automatically removed

**Configurable**: Adjust `autoCleanupMinutes` in AppGraph spec (1-60 minutes)

### Dashboard Features

#### Network Graph (D3.js)
- **Legitimate Service**: Green node (frontend-api)
- **Attacker Nodes**: Red nodes (IP addresses)
- **Decoy Nodes**: Blue nodes (3 per attacker)
- **Attack Links**: Red dashed lines (attacker → legitimate)
- **Redirect Links**: Blue solid lines (attacker → decoys)
- **Interactive**: Drag nodes, zoom, pan

#### Metrics Panel
- Total Alerts
- Active Decoys
- Blocked IPs
- Attack Types

#### Event Timeline
- Real-time event stream
- Color-coded by severity:
  - Critical: Red border
  - High: Orange border
  - Medium: Yellow border
- Auto-scroll with animation
- Limit to 50 most recent events

#### WebSocket Events

**Event Types**:
1. `alert` - Attack detected
2. `decoys_created` - Decoys deployed
3. `cleanup` - Decoys removed

**Event Schema**:
```json
{
  "type": "alert",
  "timestamp": "2026-02-04T17:00:00Z",
  "data": {
    "source_ip": "192.168.1.100",
    "attack_type": "sql_injection",
    "severity": "critical",
    "evidence": "log line"
  }
}
```

### Integration Flow

```
1. Sentinel detects attack → POST /api/alerts
2. Controller receives alert → Create AppGraph CR
3. AppGraph reconciler triggered
4. Create 3 decoy pods (staggered 0.5s)
5. Create NetworkPolicies for isolation
6. POST Manager /api/block_ip with decoy URLs
7. Manager routes attacker to decoys (round-robin)
8. WebSocket broadcast to dashboard clients
9. Dashboard updates graph and metrics
10. After 15 minutes → Auto-cleanup
```

### Resource Allocation

**Controller**:
- Memory: 100Mi (request = limit)
- CPU: 100m (request = limit)

**Decoy Pods** (3 per attack):
- Memory: 40Mi each × 3 = 120Mi
- CPU: 20m each × 3 = 60m

**Total Per Attack**: 120Mi RAM / 60m CPU for decoys

**System Total** (with 1 active attack):
| Component | Memory | CPU |
|-----------|--------|-----|
| k3s | ~800Mi | N/A |
| frontend-api | 80Mi | 50m |
| payment-svc | 40Mi | 30m |
| manager | 60Mi | 50m |
| sentinel | 80Mi | 50m |
| controller | 100Mi | 100m |
| decoys (3) | 120Mi | 60m |
| **TOTAL** | **~1.28GB** | **340m** |

**Remaining Budget**: ~1.22GB / 2.5GB

### Deployment

#### Apply CRD
```bash
kubectl apply -f deploy/k8s/appgraph-crd.yaml
```

#### Apply RBAC
```bash
kubectl apply -f deploy/k8s/controller-rbac.yaml
```

#### Deploy Controller
```bash
cd services/controller
sudo nerdctl -n k8s.io build -t controller:latest .
kubectl apply -f deploy/k8s/controller.yaml
```

#### Access Dashboard
```bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
echo "Dashboard: http://$NODE_IP:30090"
```

### Testing

#### Simulate Attack Alert
```bash
curl -X POST http://NODE_IP:30090/api/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-02-04T17:00:00Z",
    "attack_type": "sql_injection",
    "source_ip": "192.168.1.100",
    "evidence": "GET /api/products?id=1 UNION SELECT",
    "severity": "critical",
    "pod_name": "frontend-api-abc"
  }'
```

#### Check AppGraph Creation
```bash
kubectl get appgraphs
kubectl describe appgraph ag-192-168-1-100-xxxxx
```

#### View Decoy Pods
```bash
kubectl get pods -l app=decoy
kubectl logs -l app=decoy,decoy-type=logger
```

#### Watch Dashboard
```bash
# Open browser to http://NODE_IP:30090
# See real-time graph updates via WebSocket
```

---

## Phase 5 Status

**Status**: ✓ COMPLETE

**Key Achievements**:
- ✓ AppGraph CRD with v1 schema
- ✓ Controller with controller-runtime
- ✓ Decoy creation <1.5s (0.5s stagger)
- ✓ NetworkPolicy isolation
- ✓ Auto-cleanup after 15 minutes
- ✓ Dashboard on port 8090
- ✓ WebSocket real-time updates
- ✓ D3.js graph visualization
- ✓ Calls Manager /api/block_ip
- ✓ RBAC with ClusterRole

**Ready for Phase 6**: Reporter Service (Metrics Collection)

---
