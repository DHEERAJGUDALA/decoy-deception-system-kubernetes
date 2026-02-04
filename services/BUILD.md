# Build and Run Instructions - Phase 2

## Local Development

### Frontend API
```bash
cd services/frontend-api
go run cmd/main.go

# Test endpoints
curl http://localhost:8080/health
curl http://localhost:8080/api/products
```

### Payment Service
```bash
cd services/payment-svc
go run cmd/main.go

# Test endpoint
curl http://localhost:8081/health
curl -X POST http://localhost:8081/api/charge -H "Content-Type: application/json" -d '{"amount": 99.99}'
```

### With Decoy Behavior
```bash
# Slow decoy (adds 500ms latency)
IS_DECOY=true DECOY_TYPE=slow DECOY_LATENCY=500 go run cmd/main.go

# Logger decoy (verbose logging)
IS_DECOY=true DECOY_TYPE=logger DECOY_LOGGING=verbose go run cmd/main.go

# Exact decoy (normal behavior)
IS_DECOY=true DECOY_TYPE=exact go run cmd/main.go
```

## Docker Build

### Using Docker
```bash
# Build frontend-api
cd services/frontend-api
docker build -t frontend-api:latest .

# Build payment-svc
cd services/payment-svc
docker build -t payment-svc:latest .

# Verify image sizes (should be under 50MB)
docker images | grep -E "frontend-api|payment-svc"
```

### Using nerdctl (k3s native)
```bash
# Build frontend-api
cd services/frontend-api
sudo nerdctl -n k8s.io build -t frontend-api:latest .

# Build payment-svc
cd services/payment-svc
sudo nerdctl -n k8s.io build -t payment-svc:latest .

# Verify
sudo nerdctl -n k8s.io images | grep -E "frontend-api|payment-svc"
```

## Kubernetes Deployment

### Deploy to k3s
```bash
# Apply manifests
kubectl apply -f deploy/k8s/frontend-api.yaml
kubectl apply -f deploy/k8s/payment-svc.yaml

# Check deployments
kubectl get deployments
kubectl get pods
kubectl get services

# View logs
kubectl logs -l app=frontend-api
kubectl logs -l app=payment-svc
```

### Port Forward for Testing
```bash
# Frontend API
kubectl port-forward svc/frontend-api 8080:8080

# Payment Service
kubectl port-forward svc/payment-svc 8081:8081

# Test
curl http://localhost:8080/api/products
```

### Deploy Decoy Instances
```bash
# Edit the YAML and change env vars:
# IS_DECOY: "true"
# DECOY_TYPE: "slow"
# DECOY_LATENCY: "1000"

kubectl apply -f deploy/k8s/frontend-api.yaml

# Or use kubectl set env
kubectl set env deployment/frontend-api IS_DECOY=true DECOY_TYPE=slow DECOY_LATENCY=1000
kubectl set env deployment/payment-svc IS_DECOY=true DECOY_TYPE=logger
```

### Resource Usage Verification
```bash
# Check actual resource usage
kubectl top pods

# Should show:
# frontend-api: ~50-70Mi memory, ~10-30m CPU
# payment-svc: ~30-40Mi memory, ~5-20m CPU
```

## Clean Up
```bash
kubectl delete -f deploy/k8s/frontend-api.yaml
kubectl delete -f deploy/k8s/payment-svc.yaml
```
