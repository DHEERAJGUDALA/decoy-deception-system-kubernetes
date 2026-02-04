# Manager Service Usage Guide

The manager service is a reverse proxy that routes traffic based on IP blocking rules.

## Overview

- **Default Behavior**: Routes all traffic to legitimate frontend-api service
- **Blocked IPs**: Routes to decoy URLs in round-robin fashion
- **Port**: 8080 (exposed via NodePort 30000)
- **In-Memory Only**: No database, all state stored in memory

## Architecture

```
Client Request → Manager (Port 30000)
                    ↓
    ┌───────────────┴───────────────┐
    │                               │
    ↓                               ↓
Legitimate Service          Decoy Services
(frontend-api:8080)    (Round-robin to 3 URLs)
```

## API Endpoints

### Management Endpoints

#### 1. Block IP
Route an IP address to decoy services.

**Endpoint**: `POST /api/block_ip`

**Request Body**:
```json
{
  "source_ip": "192.168.1.100",
  "decoy_urls": [
    "http://decoy-frontend-1:8080",
    "http://decoy-frontend-2:8080",
    "http://decoy-frontend-3:8080"
  ]
}
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

**Example**:
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

#### 2. Cleanup IP
Remove IP from blocked list, restore routing to legitimate service.

**Endpoint**: `POST /api/cleanup`

**Request Body**:
```json
{
  "source_ip": "192.168.1.100"
}
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

**Example**:
```bash
curl -X POST http://localhost:30000/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "192.168.1.100"
  }'
```

#### 3. Health Check

**Endpoint**: `GET /health`

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

**Example**:
```bash
curl http://localhost:30000/health
```

#### 4. Statistics

**Endpoint**: `GET /api/stats`

**Response**:
```json
{
  "total_blocked_ips": 2,
  "blocked_ips": ["192.168.1.100", "10.0.0.50"]
}
```

**Example**:
```bash
curl http://localhost:30000/api/stats
```

### Reverse Proxy Endpoints

All other requests are reverse-proxied:
- **Legitimate IPs**: Forwarded to `http://frontend-api:8080`
- **Blocked IPs**: Forwarded to decoy URLs (round-robin)

## Round-Robin Routing

When an IP is blocked with 3 decoy URLs:

1. **Request 1**: Routes to `decoy_urls[0]`
2. **Request 2**: Routes to `decoy_urls[1]`
3. **Request 3**: Routes to `decoy_urls[2]`
4. **Request 4**: Routes to `decoy_urls[0]` (cycles back)
5. And so on...

This ensures equal distribution across all decoy services.

## Structured Logging

All routing decisions are logged in JSON format:

### Block IP Event
```json
{
  "timestamp": "2026-02-04T15:30:00Z",
  "action": "block_ip",
  "source_ip": "192.168.1.100",
  "decoy_urls": ["http://decoy-1:8080", "http://decoy-2:8080", "http://decoy-3:8080"]
}
```

### Route to Decoy Event
```json
{
  "timestamp": "2026-02-04T15:31:00Z",
  "action": "route_to_decoy",
  "source_ip": "192.168.1.100",
  "selected_url": "http://decoy-1:8080",
  "round_robin_count": 1
}
```

### Route to Legitimate Event
```json
{
  "timestamp": "2026-02-04T15:32:00Z",
  "action": "route_to_legitimate",
  "source_ip": "192.168.1.200",
  "method": "GET",
  "path": "/api/products"
}
```

### Cleanup Event
```json
{
  "timestamp": "2026-02-04T15:33:00Z",
  "action": "cleanup_ip",
  "source_ip": "192.168.1.100"
}
```

## Local Testing

### Start Manager Locally
```bash
cd services/manager
LEGITIMATE_SERVICE_URL=http://localhost:8080 go run cmd/main.go
```

### Test Workflow

1. **Normal Request (Not Blocked)**:
```bash
curl http://localhost:8080/api/products
# Routes to legitimate frontend-api
```

2. **Block an IP**:
```bash
curl -X POST http://localhost:8080/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "127.0.0.1",
    "decoy_urls": [
      "http://localhost:9001",
      "http://localhost:9002",
      "http://localhost:9003"
    ]
  }'
```

3. **Subsequent Requests (Blocked IP)**:
```bash
curl http://localhost:8080/api/products
# Routes to decoy in round-robin fashion
```

4. **Cleanup IP**:
```bash
curl -X POST http://localhost:8080/api/cleanup \
  -H "Content-Type: application/json" \
  -d '{"source_ip": "127.0.0.1"}'
```

5. **Request After Cleanup**:
```bash
curl http://localhost:8080/api/products
# Routes back to legitimate service
```

## Kubernetes Deployment

### Build and Deploy
```bash
# Build image
cd services/manager
sudo nerdctl -n k8s.io build -t manager:latest .

# Deploy
kubectl apply -f deploy/k8s/manager.yaml

# Verify
kubectl get pods -l app=manager
kubectl get svc manager
```

### Access via NodePort
```bash
# Get node IP
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')

# Test health
curl http://$NODE_IP:30000/health

# Block an IP
curl -X POST http://$NODE_IP:30000/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "10.0.0.50",
    "decoy_urls": [
      "http://decoy-frontend-1:8080",
      "http://decoy-frontend-2:8080",
      "http://decoy-frontend-3:8080"
    ]
  }'
```

## Integration with Sentinel (Phase 4+)

The Sentinel service will call manager's API to block suspicious IPs:

```bash
# Sentinel detects attacker and blocks IP
curl -X POST http://manager:8080/api/block_ip \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "attacker-ip",
    "decoy_urls": ["http://decoy1:8080", "http://decoy2:8080", "http://decoy3:8080"]
  }'
```

## Environment Variables

- `PORT`: Manager service port (default: 8080)
- `LEGITIMATE_SERVICE_URL`: URL of legitimate service (default: http://frontend-api:8080)

## Source IP Detection

The manager extracts source IP in this order:
1. `X-Forwarded-For` header (if present)
2. `X-Real-IP` header (if present)
3. `RemoteAddr` (fallback)

## Memory Usage

In-memory storage only:
- Each blocked IP entry: ~200 bytes
- 1000 blocked IPs: ~200KB
- Service overhead: ~60Mi (as per resource limits)

## Notes

- No Kubernetes Services are used for decoy routing
- All routing decisions are in-memory
- State is lost on pod restart (by design)
- Round-robin counter increments indefinitely (integer overflow after ~2 billion requests)
