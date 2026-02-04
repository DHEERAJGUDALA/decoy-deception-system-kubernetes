# Phase 2 Completion Summary

## Services Implemented

### 1. frontend-api (Port 8080)
**Location**: `services/frontend-api/`

**Endpoints**:
- `GET /` - HTML homepage with endpoint documentation
- `GET /health` - Health check
- `GET /api/products` - List 4 mock products
- `GET /api/cart` - Cart management
- `POST /api/login` - User authentication (mock JWT)
- `POST /api/checkout` - Process order and call payment-svc

**Features**:
- Embedded static HTML (no external files)
- JSON structured logging (timestamp, method, path, source_ip)
- Async metrics push to reporter-service
- Decoy behavior support (exact/slow/logger)
- Resource limits: 80Mi memory, 50m CPU

### 2. payment-svc (Port 8081)
**Location**: `services/payment-svc/`

**Endpoints**:
- `GET /health` - Health check
- `POST /api/charge` - Process payment

**Features**:
- Generates unique transaction IDs (base36 timestamp)
- JSON structured logging
- Decoy behavior support
- Resource limits: 40Mi memory, 30m CPU

## Decoy Configuration

All services support environment-based decoy behavior:

```bash
# Environment Variables
IS_DECOY=true|false          # Enable decoy mode
DECOY_TYPE=exact|slow|logger # Behavior type
DECOY_LATENCY=<ms>          # Latency for slow mode
DECOY_LOGGING=normal|verbose # Log verbosity
```

### Decoy Types

1. **exact**: Behaves normally, indistinguishable from real service
2. **slow**: Adds artificial latency (e.g., 500-2000ms)
3. **logger**: Verbose logging of all requests/responses

## Docker Images

Both services use multi-stage builds:
- Builder: golang:1.21-alpine
- Final: alpine:latest
- CGO disabled (static binaries)
- Binary stripping enabled (-w -s flags)
- Non-root user (appuser:1000)

**Expected Sizes**:
- frontend-api: ~15-20MB
- payment-svc: ~12-15MB

## Kubernetes Deployment

**Manifests**: `deploy/k8s/`

Both services include:
- Deployment with replica count 1
- ClusterIP Service
- Liveness probe (10s initial, 30s period)
- Readiness probe (5s initial, 10s period)
- Resource requests = limits (guaranteed QoS)

**Resource Allocation**:
- frontend-api: 80Mi RAM / 50m CPU
- payment-svc: 40Mi RAM / 30m CPU
- **Total: 120Mi RAM / 80m CPU**

## Quick Start

### Local Development
```bash
# Terminal 1 - Payment Service
cd services/payment-svc
go run cmd/main.go

# Terminal 2 - Frontend API
cd services/frontend-api
PAYMENT_SERVICE_URL=http://localhost:8081/api/charge go run cmd/main.go

# Test
curl http://localhost:8080/health
curl http://localhost:8080/api/products
```

### Docker Build
```bash
# Frontend API
cd services/frontend-api
docker build -t frontend-api:latest .

# Payment Service
cd services/payment-svc
docker build -t payment-svc:latest .

# Check sizes
docker images | grep -E "frontend-api|payment-svc"
```

### k3s Deployment
```bash
# Build for k3s (using nerdctl)
cd services/frontend-api
sudo nerdctl -n k8s.io build -t frontend-api:latest .

cd ../payment-svc
sudo nerdctl -n k8s.io build -t payment-svc:latest .

# Deploy
kubectl apply -f deploy/k8s/frontend-api.yaml
kubectl apply -f deploy/k8s/payment-svc.yaml

# Verify
kubectl get pods
kubectl get svc

# Port-forward for testing
kubectl port-forward svc/frontend-api 8080:8080
```

### Deploy as Decoy
```bash
# Update deployment with decoy settings
kubectl set env deployment/frontend-api \
  IS_DECOY=true \
  DECOY_TYPE=slow \
  DECOY_LATENCY=1000 \
  DECOY_LOGGING=verbose

kubectl set env deployment/payment-svc \
  IS_DECOY=true \
  DECOY_TYPE=logger \
  DECOY_LOGGING=verbose

# Watch rollout
kubectl rollout status deployment/frontend-api
kubectl rollout status deployment/payment-svc

# Check logs
kubectl logs -l app=frontend-api -f
```

## Testing Decoy Behavior

### Exact Decoy (Normal)
```bash
IS_DECOY=true DECOY_TYPE=exact go run cmd/main.go
# Behaves identically to production
```

### Slow Decoy (Latency)
```bash
IS_DECOY=true DECOY_TYPE=slow DECOY_LATENCY=2000 go run cmd/main.go
curl http://localhost:8080/api/products
# Response delayed by 2000ms
```

### Logger Decoy (Verbose)
```bash
IS_DECOY=true DECOY_TYPE=logger DECOY_LOGGING=verbose go run cmd/main.go
curl -X POST http://localhost:8080/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"pass"}'
# Check logs for [VERBOSE] entries
```

## Memory Budget Compliance

| Component | Memory Limit | CPU Limit | Status |
|-----------|--------------|-----------|--------|
| k3s | ~800Mi | N/A | Phase 1 |
| frontend-api | 80Mi | 50m | ✓ |
| payment-svc | 40Mi | 30m | ✓ |
| **TOTAL** | **~920Mi** | **80m** | **Within 2.5GB budget** |

## Files Created

```
services/
├── BUILD.md                          # Build and deployment instructions
├── frontend-api/
│   ├── go.mod                        # Go module
│   ├── Dockerfile                    # Multi-stage Alpine build
│   └── cmd/
│       └── main.go                   # Service implementation (370 lines)
└── payment-svc/
    ├── go.mod                        # Go module
    ├── Dockerfile                    # Multi-stage Alpine build
    └── cmd/
        └── main.go                   # Service implementation (160 lines)

deploy/
└── k8s/
    ├── frontend-api.yaml             # K8s Deployment + Service
    └── payment-svc.yaml              # K8s Deployment + Service
```

## Next Steps (Phase 3+)

Phase 2 services are ready for:
- Reporter service integration (metrics ingestion)
- Traffic routing and load balancing
- Real vs. decoy traffic distribution
- Anomaly detection based on interaction patterns

---

**Phase 2 Status**: ✓ COMPLETE
**Documentation**: See ACTION_LOG.md for detailed implementation notes
