# Phase 3 - Manager Service Examples

Complete curl examples for testing the manager service.

## Prerequisites

```bash
# Get node IP for NodePort access
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
echo "Node IP: $NODE_IP"

# Or for local testing
MANAGER_URL="http://localhost:8080"
```

## Example 1: Basic Health Check

```bash
# Health check
curl http://$NODE_IP:30000/health

# Expected response:
{
  "status": "healthy",
  "service": "manager",
  "stats": {
    "total_blocked_ips": 0,
    "blocked_ips": []
  }
}
```

## Example 2: Block Single IP with 3 Decoys

```bash
# Block IP 192.168.1.100
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100",
    "decoy_urls": [
      "http://decoy-frontend-1:8080",
      "http://decoy-frontend-2:8080",
      "http://decoy-frontend-3:8080"
    ]
  }'

# Expected response:
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

## Example 3: Test Round-Robin Routing

After blocking an IP, simulate requests from that IP:

```bash
# Request 1 - Routes to decoy-1
curl -H "X-Forwarded-For: 192.168.1.100" \
  http://$NODE_IP:30000/api/products

# Request 2 - Routes to decoy-2
curl -H "X-Forwarded-For: 192.168.1.100" \
  http://$NODE_IP:30000/api/cart

# Request 3 - Routes to decoy-3
curl -H "X-Forwarded-For: 192.168.1.100" \
  http://$NODE_IP:30000/api/login

# Request 4 - Routes back to decoy-1 (cycle)
curl -H "X-Forwarded-For: 192.168.1.100" \
  http://$NODE_IP:30000/health

# Check manager logs to see round-robin routing
kubectl logs -l app=manager | grep route_to_decoy
```

## Example 4: Check Statistics

```bash
# Get current stats
curl http://$NODE_IP:30000/api/stats

# Expected response:
{
  "total_blocked_ips": 1,
  "blocked_ips": ["192.168.1.100"]
}
```

## Example 5: Block Multiple IPs

```bash
# Block second IP
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "10.0.0.50",
    "decoy_urls": [
      "http://decoy-a:8080",
      "http://decoy-b:8080",
      "http://decoy-c:8080"
    ]
  }'

# Block third IP
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "172.16.0.10",
    "decoy_urls": [
      "http://decoy-x:8080",
      "http://decoy-y:8080",
      "http://decoy-z:8080"
    ]
  }'

# Check stats
curl http://$NODE_IP:30000/api/stats

# Expected:
{
  "total_blocked_ips": 3,
  "blocked_ips": ["192.168.1.100", "10.0.0.50", "172.16.0.10"]
}
```

## Example 6: Cleanup (Unblock) an IP

```bash
# Remove IP from blocklist
curl -X POST http://$NODE_IP:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100"
  }'

# Expected response:
{
  "success": true,
  "message": "IP 192.168.1.100 cleanup result",
  "source_ip": "192.168.1.100",
  "removed": true
}

# Verify cleanup
curl http://$NODE_IP:30000/api/stats

# Expected (IP removed):
{
  "total_blocked_ips": 2,
  "blocked_ips": ["10.0.0.50", "172.16.0.10"]
}
```

## Example 7: Cleanup Non-Existent IP

```bash
# Try to cleanup IP that's not blocked
curl -X POST http://$NODE_IP:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "1.2.3.4"
  }'

# Expected response:
{
  "success": false,
  "message": "IP 1.2.3.4 cleanup result",
  "source_ip": "1.2.3.4",
  "removed": false
}
```

## Example 8: Test Legitimate Traffic Routing

```bash
# Request from non-blocked IP
curl -H "X-Forwarded-For: 8.8.8.8" \
  http://$NODE_IP:30000/api/products

# This should route to legitimate frontend-api service
# Check manager logs:
kubectl logs -l app=manager | grep route_to_legitimate

# Expected log entry:
{
  "timestamp": "2026-02-04T15:45:00Z",
  "action": "route_to_legitimate",
  "source_ip": "8.8.8.8",
  "method": "GET",
  "path": "/api/products"
}
```

## Example 9: Invalid Requests (Error Cases)

```bash
# Missing source_ip
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "decoy_urls": ["http://decoy:8080"]
  }'
# Expected: 400 Bad Request - "source_ip is required"

# Missing decoy_urls
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "1.2.3.4"
  }'
# Expected: 400 Bad Request - "decoy_urls array is required"

# Invalid JSON
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d 'invalid json'
# Expected: 400 Bad Request - "Invalid request body"

# Wrong HTTP method
curl -X GET http://$NODE_IP:30000/api/block_ip
# Expected: 405 Method Not Allowed
```

## Example 10: Complete Test Workflow

```bash
#!/bin/bash
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
MANAGER_URL="http://$NODE_IP:30000"

echo "Step 1: Check initial health"
curl -s $MANAGER_URL/health | jq .

echo -e "\nStep 2: Block attacker IP"
curl -s -X POST $MANAGER_URL/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100",
    "decoy_urls": [
      "http://decoy-1:8080",
      "http://decoy-2:8080",
      "http://decoy-3:8080"
    ]
  }' | jq .

echo -e "\nStep 3: Check stats"
curl -s $MANAGER_URL/api/stats | jq .

echo -e "\nStep 4: Test round-robin (3 requests)"
for i in {1..3}; do
  echo "Request $i:"
  curl -s -H "X-Forwarded-For: 192.168.1.100" $MANAGER_URL/api/products
  echo ""
done

echo -e "\nStep 5: Check manager logs for routing"
kubectl logs -l app=manager --tail=10 | grep route_to_decoy

echo -e "\nStep 6: Cleanup IP"
curl -s -X POST $MANAGER_URL/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{"source_ip": "192.168.1.100"}' | jq .

echo -e "\nStep 7: Verify cleanup"
curl -s $MANAGER_URL/api/stats | jq .

echo -e "\nStep 8: Test request after cleanup (should go to legit service)"
curl -s -H "X-Forwarded-For: 192.168.1.100" $MANAGER_URL/api/products

echo -e "\nTest complete!"
```

## Example 11: Monitor Logs in Real-Time

```bash
# Watch manager logs
kubectl logs -f -l app=manager

# In another terminal, send requests
curl -H "X-Forwarded-For: 192.168.1.100" http://$NODE_IP:30000/api/products

# You'll see JSON logs like:
{
  "timestamp": "2026-02-04T15:50:00Z",
  "action": "route_to_decoy",
  "source_ip": "192.168.1.100",
  "selected_url": "http://decoy-1:8080",
  "round_robin_count": 1
}
```

## Example 12: Integration Test with Decoy Services

```bash
# Assuming you have 3 decoy frontend-api instances running

# Deploy decoys (use Phase 2 manifests with different names)
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: decoy-frontend-1
spec:
  replicas: 1
  selector:
    matchLabels:
      app: decoy-frontend-1
  template:
    metadata:
      labels:
        app: decoy-frontend-1
    spec:
      containers:
      - name: frontend-api
        image: frontend-api:latest
        env:
        - name: IS_DECOY
          value: "true"
        - name: DECOY_TYPE
          value: "slow"
        - name: DECOY_LATENCY
          value: "1000"
        resources:
          limits:
            memory: 80Mi
            cpu: 50m
---
apiVersion: v1
kind: Service
metadata:
  name: decoy-frontend-1
spec:
  selector:
    app: decoy-frontend-1
  ports:
  - port: 8080
