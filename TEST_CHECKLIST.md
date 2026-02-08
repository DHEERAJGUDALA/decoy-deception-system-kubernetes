# Test Checklist - Decoy Deception System

This document provides a comprehensive test checklist for validating the Decoy Deception System functionality, performance, and dashboard features.

## Pre-Deployment Tests

### Environment Validation

- [ ] **WSL Detection**
  ```bash
  grep -qi microsoft /proc/version && echo "WSL detected" || echo "Not WSL"
  ```
  Expected: "WSL detected"

- [ ] **Memory Available**
  ```bash
  free -h | grep Mem | awk '{print $7}'
  ```
  Expected: At least 2.5GB available

- [ ] **Dependencies Installed**
  ```bash
  make check
  ```
  Expected: ✓ go found, ✓ docker found, ✓ All dependencies satisfied

- [ ] **k3s Installation**
  ```bash
  make setup
  ```
  Expected: ✓ k3s installed successfully

- [ ] **k3s Verification**
  ```bash
  make verify
  ```
  Expected: ✓ k3s is running, Node: ready, k3s memory usage: <800MB

## Deployment Tests

### Image Building

- [ ] **Build All Images**
  ```bash
  make build
  ```
  Expected: All 6 images built successfully (frontend-api, payment-svc, manager, sentinel, controller, reporter)

- [ ] **Verify Image Sizes**
  ```bash
  docker images | grep -E 'frontend-api|payment-svc|manager|sentinel|controller|reporter'
  ```
  Expected sizes:
  - frontend-api: ~15-20MB
  - payment-svc: ~12-15MB
  - manager: ~11-15MB
  - sentinel: ~40-45MB
  - controller: ~45-50MB
  - reporter: ~10-12MB

### Image Import (WSL-Critical)

- [ ] **Images Imported to k3s**
  ```bash
  sudo k3s ctr images ls | grep -E 'frontend-api|payment-svc|manager|sentinel|controller|reporter'
  ```
  Expected: All 6 images present in k3s containerd

### Service Deployment

- [ ] **Full Deployment**
  ```bash
  make deploy
  ```
  Expected: Deployment Complete! ✓ with endpoints displayed

- [ ] **All Pods Running**
  ```bash
  kubectl get pods
  ```
  Expected: 6/6 pods in Running status

- [ ] **All Services Created**
  ```bash
  kubectl get svc
  ```
  Expected: 7 services (6 app services + kubernetes)

- [ ] **CRD Installed**
  ```bash
  kubectl get crd appgraphs.deception.demo
  ```
  Expected: CRD found

- [ ] **RBAC Configured**
  ```bash
  kubectl get serviceaccount,role,rolebinding,clusterrole,clusterrolebinding | grep -E 'sentinel|controller'
  ```
  Expected: Sentinel SA/Role/RoleBinding, Controller SA/ClusterRole/ClusterRoleBinding

- [ ] **ConfigMap Present**
  ```bash
  kubectl get configmap sentinel-config
  ```
  Expected: ConfigMap found

## Functional Tests

### Normal Traffic

- [ ] **Run Normal Traffic Test**
  ```bash
  make test-normal
  ```
  Expected: 20 requests sent, HTTP 200 responses, ~60 seconds duration

- [ ] **No Alerts Generated**
  ```bash
  kubectl logs -l app=sentinel | grep -c attack_detected
  ```
  Expected: 0 alerts

- [ ] **Metrics Collected**
  ```bash
  kubectl port-forward svc/reporter 8080:8080 &
  curl -s http://localhost:8080/api/stats | jq '.total_requests'
  ```
  Expected: ~20 requests

- [ ] **Traffic Routed to Frontend-API**
  ```bash
  curl -s http://localhost:8080/api/stats | jq '.requests_by_service["frontend-api"]'
  ```
  Expected: ~20 requests

### SQL Injection Detection

- [ ] **Run SQLi Attack**
  ```bash
  make test-sqli
  ```
  Expected: 30 attacks sent, ~10 seconds duration

- [ ] **Sentinel Detects SQLi**
  ```bash
  kubectl logs -l app=sentinel | grep sql_injection
  ```
  Expected: At least 1 "sql_injection" log entry with severity "critical"

- [ ] **Alert Sent to Controller**
  ```bash
  kubectl logs -l app=controller | grep alert
  ```
  Expected: Alert received log entry

- [ ] **AppGraph Created**
  ```bash
  kubectl get appgraph
  ```
  Expected: 1 AppGraph CR

- [ ] **3 Decoy Pods Created**
  ```bash
  kubectl get pods -l decoy=true --no-headers | wc -l
  ```
  Expected: 3 pods

- [ ] **Decoy Pods Running**
  ```bash
  kubectl get pods -l decoy=true
  ```
  Expected: All 3 pods in Running status

- [ ] **Decoy Types Correct**
  ```bash
  kubectl get pods -l decoy=true -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
  ```
  Expected: decoy-frontend-1, decoy-frontend-2, decoy-frontend-3

- [ ] **IP Blocked by Manager**
  ```bash
  kubectl logs -l app=manager | grep block_ip
  ```
  Expected: IP block log with 3 decoy URLs

- [ ] **NetworkPolicies Created**
  ```bash
  kubectl get networkpolicy -l decoy=true --no-headers | wc -l
  ```
  Expected: 3 NetworkPolicies

- [ ] **Services Created for Decoys**
  ```bash
  kubectl get svc -l decoy=true --no-headers | wc -l
  ```
  Expected: 3 Services

### Rate Limit Detection

- [ ] **Run Rate Limit Attack**
  ```bash
  make test-rate
  ```
  Expected: 70 requests sent, ~5 seconds duration, rate ~1200 req/min

- [ ] **Sentinel Detects Rate Limit**
  ```bash
  kubectl logs -l app=sentinel | grep rate_limit_exceeded
  ```
  Expected: At least 1 "rate_limit_exceeded" log entry with severity "medium"

- [ ] **Evidence Shows Request Count**
  ```bash
  kubectl logs -l app=sentinel | grep rate_limit_exceeded | grep -o 'requests in [0-9]* minute'
  ```
  Expected: Shows >50 requests in 1 minute

### Round-Robin Routing

- [ ] **Multiple Requests After Block**
  ```bash
  # After SQLi attack, send more requests
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
  for i in {1..9}; do curl -s "http://$NODE_IP:30000/api/products" > /dev/null; done
  ```

- [ ] **Verify Round-Robin in Manager Logs**
  ```bash
  kubectl logs -l app=manager | grep route_to_decoy | tail -9
  ```
  Expected: Different decoy URLs in rotation (decoy-frontend-1, 2, 3, 1, 2, 3, ...)

- [ ] **Decoys Receive Requests**
  ```bash
  kubectl port-forward svc/reporter 8080:8080 &
  curl -s http://localhost:8080/api/services | jq 'to_entries[] | select(.key | startswith("decoy")) | {service: .key, requests: .value.total_requests}'
  ```
  Expected: All 3 decoys have >0 requests

### Auto-Cleanup

- [ ] **Check Cleanup Schedule**
  ```bash
  kubectl get appgraph -o jsonpath='{.items[0].status.cleanupScheduledAt}'
  ```
  Expected: Timestamp ~15 minutes in the future

- [ ] **Wait for Cleanup (15 minutes)**
  ```bash
  # Wait 15 minutes or adjust for testing
  sleep 900
  ```

- [ ] **AppGraph Deleted**
  ```bash
  kubectl get appgraph
  ```
  Expected: No resources found

- [ ] **Decoy Pods Deleted (Cascade)**
  ```bash
  kubectl get pods -l decoy=true
  ```
  Expected: No resources found

- [ ] **NetworkPolicies Deleted**
  ```bash
  kubectl get networkpolicy -l decoy=true
  ```
  Expected: No resources found

- [ ] **Services Deleted**
  ```bash
  kubectl get svc -l decoy=true
  ```
  Expected: No resources found

## Performance Tests

### Resource Usage

- [ ] **k3s Memory Usage**
  ```bash
  ps aux | grep k3s | awk '{sum+=$6} END {print sum/1024 " MB"}'
  ```
  Expected: <800MB

- [ ] **Pod Memory Usage**
  ```bash
  kubectl top pods 2>/dev/null || echo "metrics-server not available (expected for k3s)"
  ```
  Expected: All pods within limits (or skip if metrics-server disabled)

- [ ] **Total System Memory**
  ```bash
  free -h | grep Mem
  ```
  Expected: <1.34GB used (including k3s + all services)

### Latency Tests

- [ ] **Metric Ingestion Latency**
  ```bash
  time curl -s -X POST http://localhost:8080/api/ingest \
    -H "Content-Type: application/json" \
    -d '{"service":"test","method":"GET","path":"/test","source_ip":"127.0.0.1","status_code":200,"latency_ms":10}'
  ```
  Expected: <10ms (real time)

- [ ] **Stats Aggregation Latency**
  ```bash
  time curl -s http://localhost:8080/api/stats > /dev/null
  ```
  Expected: <50ms for <1000 metrics

- [ ] **Detection Latency (SQLi)**
  ```bash
  # Send attack and measure time to alert
  START=$(date +%s)
  make test-sqli > /dev/null 2>&1
  kubectl wait --for=condition=ready pod -l decoy=true --timeout=10s 2>/dev/null
  END=$(date +%s)
  echo "Detection to deployment: $((END - START)) seconds"
  ```
  Expected: <5 seconds

- [ ] **Decoy Deployment Time**
  ```bash
  # Check time between AppGraph creation and pod ready
  kubectl get appgraph -o jsonpath='{.items[0].metadata.creationTimestamp}'
  kubectl get pods -l decoy=true -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].lastTransitionTime}'
  ```
  Expected: <2 seconds difference

### Throughput Tests

- [ ] **Reporter Ingestion Throughput**
  ```bash
  # Send 100 metrics rapidly
  for i in {1..100}; do
    curl -s -X POST http://localhost:8080/api/ingest \
      -H "Content-Type: application/json" \
      -d "{\"service\":\"test\",\"method\":\"GET\",\"path\":\"/test\",\"source_ip\":\"127.0.0.$i\",\"status_code\":200,\"latency_ms\":10}" &
  done
  wait

  # Verify all ingested
  curl -s http://localhost:8080/api/stats | jq '.total_requests'
  ```
  Expected: ~120 requests (20 from normal + 100 from test)

- [ ] **Manager Proxy Throughput**
  ```bash
  # Send 50 concurrent requests
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
  for i in {1..50}; do
    curl -s "http://$NODE_IP:30000/api/products" > /dev/null &
  done
  wait
  ```
  Expected: All requests complete successfully

## Dashboard Tests

### Dashboard Access

- [ ] **Dashboard Accessible**
  ```bash
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
  curl -s "http://$NODE_IP:30090/" | grep -q "Decoy Deception Dashboard"
  ```
  Expected: Dashboard HTML returned

- [ ] **WebSocket Endpoint Available**
  ```bash
  curl -s -H "Upgrade: websocket" "http://$NODE_IP:30090/ws" -i | grep -q "101"
  ```
  Expected: HTTP 101 Switching Protocols (or connection attempt)

### Dashboard Visualization

**Manual Tests** (open dashboard in browser):

- [ ] **Dashboard Opens**
  ```bash
  make dashboard
  ```
  Expected: Browser opens to http://NODE_IP:30090

- [ ] **Topology Graph Visible**
  - Expected: SVG graph with nodes and links

- [ ] **Manager Node Present**
  - Expected: Blue node labeled "Manager" in center

- [ ] **Frontend-API Node Present**
  - Expected: Green node labeled "Frontend-API"

- [ ] **Link Between Manager and Frontend-API**
  - Expected: Line connecting the two nodes

- [ ] **Metrics Panel Visible**
  - Expected: Panel showing AppGraphs, Decoys, Alerts counts

- [ ] **Event Timeline Visible**
  - Expected: List of recent events with timestamps

### Dashboard Real-Time Updates

**Run attack and observe dashboard:**

- [ ] **Run SQLi Attack While Watching Dashboard**
  ```bash
  make test-sqli
  ```

- [ ] **Decoy Nodes Appear**
  - Expected: 3 orange nodes labeled "Decoy-1", "Decoy-2", "Decoy-3" appear within 2-3 seconds

- [ ] **Links to Decoys Appear**
  - Expected: Lines connecting Manager to all 3 decoy nodes

- [ ] **Metrics Panel Updates**
  - Expected: AppGraphs count increases to 1, Decoys count increases to 3

- [ ] **Event Timeline Updates**
  - Expected: New events appear:
    - "AppGraph Created: decoy-app"
    - "Pod Created: decoy-frontend-1"
    - "Pod Created: decoy-frontend-2"
    - "Pod Created: decoy-frontend-3"

- [ ] **Node Drag Interaction**
  - Expected: Can click and drag nodes, force simulation adjusts

- [ ] **Auto-Reconnect After Refresh**
  - Expected: Refresh page, WebSocket reconnects, graph reloads

### Dashboard After Cleanup

- [ ] **Wait for Auto-Cleanup (or delete manually)**
  ```bash
  kubectl delete appgraph --all
  ```

- [ ] **Decoy Nodes Disappear**
  - Expected: Orange decoy nodes removed from graph

- [ ] **Links to Decoys Disappear**
  - Expected: Lines to decoys removed

- [ ] **Metrics Panel Updates**
  - Expected: AppGraphs count decreases to 0, Decoys count decreases to 0

- [ ] **Cleanup Event in Timeline**
  - Expected: "AppGraph Deleted: decoy-app" event

## Integration Tests

### End-to-End Flow

- [ ] **Full Attack-to-Cleanup Flow**
  1. Deploy system: `make deploy`
  2. Normal traffic: `make test-normal`
  3. Verify no alerts: `kubectl logs -l app=sentinel | grep -c attack_detected` (0)
  4. SQLi attack: `make test-sqli`
  5. Verify detection: `kubectl logs -l app=sentinel | grep sql_injection` (found)
  6. Verify decoys: `kubectl get pods -l decoy=true` (3 pods)
  7. Verify routing: `kubectl logs -l app=manager | grep route_to_decoy` (found)
  8. Verify metrics: `curl http://localhost:8080/api/stats | jq '.requests_by_service'` (shows decoys)
  9. Wait cleanup: `sleep 900` or `kubectl delete appgraph --all`
  10. Verify cleanup: `kubectl get pods -l decoy=true` (no resources)

### Reporter Integration

- [ ] **Frontend-API Reports Metrics**
  ```bash
  kubectl logs -l app=frontend-api | grep -c "sending metric"
  ```
  Expected: >0 (if verbose logging enabled) or check Reporter stats

- [ ] **Decoys Report Metrics**
  ```bash
  curl -s http://localhost:8080/api/services | jq 'keys[]' | grep decoy
  ```
  Expected: decoy-frontend-1, decoy-frontend-2, decoy-frontend-3 present

- [ ] **Reporter Aggregates Correctly**
  ```bash
  TOTAL=$(curl -s http://localhost:8080/api/stats | jq '.total_requests')
  BY_SERVICE=$(curl -s http://localhost:8080/api/stats | jq '.requests_by_service | to_entries | map(.value) | add')
  [ "$TOTAL" -eq "$BY_SERVICE" ] && echo "PASS" || echo "FAIL: Mismatch"
  ```
  Expected: PASS (total equals sum of by-service)

### Sentinel Integration

- [ ] **Sentinel Watches Frontend-API Logs**
  ```bash
  kubectl logs -l app=sentinel | grep "Watching pods"
  ```
  Expected: Log showing pod watch started

- [ ] **Sentinel Extracts Source IP**
  ```bash
  kubectl logs -l app=sentinel | grep "Extracted IP"
  ```
  Expected: IP extraction logs (if verbose) or successful detection

- [ ] **Sentinel Sends Alert to Controller**
  ```bash
  kubectl logs -l app=controller | grep "Received alert from Sentinel"
  ```
  Expected: Alert received log

### Controller Integration

- [ ] **Controller Creates AppGraph**
  ```bash
  kubectl get appgraph -o jsonpath='{.items[0].metadata.name}'
  ```
  Expected: decoy-app (or similar)

- [ ] **Controller Calls Manager API**
  ```bash
  kubectl logs -l app=manager | grep "POST /api/block_ip"
  ```
  Expected: Manager received block_ip request

- [ ] **Controller Updates AppGraph Status**
  ```bash
  kubectl get appgraph -o jsonpath='{.items[0].status.decoyStatus}'
  ```
  Expected: Active or similar status

### Manager Integration

- [ ] **Manager Routes to Frontend-API (Normal)**
  ```bash
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
  curl -s "http://$NODE_IP:30000/api/products" | grep -q "product"
  ```
  Expected: Response from frontend-api

- [ ] **Manager Routes to Decoys (Blocked)**
  ```bash
  # After attack, check routing
  kubectl logs -l app=manager | grep route_to_decoy | tail -1
  ```
  Expected: Routing to decoy URL

## Cleanup and Teardown Tests

- [ ] **Clean Deployment**
  ```bash
  make clean-deploy
  ```
  Expected: All deployments removed, pods terminated

- [ ] **All Pods Deleted**
  ```bash
  kubectl get pods
  ```
  Expected: No pods (or only system pods)

- [ ] **All Services Deleted**
  ```bash
  kubectl get svc
  ```
  Expected: Only kubernetes service

- [ ] **CRD Still Present (Not Deleted by clean-deploy)**
  ```bash
  kubectl get crd appgraphs.deception.demo
  ```
  Expected: CRD found (clean-deploy doesn't remove CRD)

- [ ] **Images Still in k3s**
  ```bash
  sudo k3s ctr images ls | grep -c frontend-api
  ```
  Expected: 1 (images retained for faster re-deploy)

- [ ] **Clean Images**
  ```bash
  make clean-images
  ```
  Expected: Docker images removed

- [ ] **Images Removed from Docker**
  ```bash
  docker images | grep -c frontend-api
  ```
  Expected: 0

## Test Summary

### Pass/Fail Criteria

**Critical Tests (Must Pass):**
- [ ] All pods running after deployment
- [ ] SQLi attack detected by Sentinel
- [ ] Decoy pods created automatically
- [ ] Round-robin routing works
- [ ] Dashboard accessible and shows topology
- [ ] Auto-cleanup removes AppGraphs and decoys

**Important Tests (Should Pass):**
- [ ] Normal traffic not flagged
- [ ] Rate limit detection works
- [ ] Metrics collected by Reporter
- [ ] All services within memory limits
- [ ] Images imported to k3s correctly

**Optional Tests (Nice to Have):**
- [ ] WebSocket auto-reconnect
- [ ] Dashboard drag interaction
- [ ] Throughput >100 req/sec
- [ ] Detection latency <3 seconds

### Test Results Template

```
Test Date: ____________________
Tester: ____________________
Environment: WSL / Linux / Other

Pre-Deployment: ___/3 passed
Deployment: ___/10 passed
Functional: ___/30 passed
Performance: ___/8 passed
Dashboard: ___/15 passed
Integration: ___/10 passed
Cleanup: ___/6 passed

Total: ___/82 passed

Critical Issues:
-
-

Recommendations:
-
-
```

## Automated Test Script

Create `scripts/run-tests.sh` for automated testing:

```bash
#!/bin/bash
# Automated test runner

PASSED=0
FAILED=0

test() {
  local name="$1"
  local cmd="$2"

  echo -n "Testing: $name... "
  if eval "$cmd" > /dev/null 2>&1; then
    echo "PASS"
    PASSED=$((PASSED + 1))
  else
    echo "FAIL"
    FAILED=$((FAILED + 1))
  fi
}

# Run tests
test "k3s running" "kubectl get nodes"
test "All pods running" "[ \$(kubectl get pods --no-headers | grep -c Running) -eq 6 ]"
test "SQLi detection" "make test-sqli && sleep 5 && kubectl logs -l app=sentinel | grep -q sql_injection"
test "Decoys created" "[ \$(kubectl get pods -l decoy=true --no-headers | wc -l) -eq 3 ]"

echo ""
echo "Results: $PASSED passed, $FAILED failed"
```

## Continuous Testing

For ongoing validation:

```bash
# Run full test suite
make test          # Normal + SQLi + Rate attacks

# Monitor logs
make logs          # Tail Sentinel logs

# Check dashboard
make dashboard     # Open dashboard

# Verify cleanup
sleep 900 && kubectl get appgraph  # Should be empty after 15 min
```
