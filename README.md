# Deception System

A Kubernetes-based deception platform that protects a real e-commerce workload by detecting malicious traffic, spawning decoys on demand, and redirecting attackers away from production services.

## 1. Project Overview

This project deploys a full demonstration environment on local Kubernetes (Minikube on Docker Engine by default, or K3s) with four isolated namespaces: a real e-commerce stack, a deception gateway, a decoy pool, and a monitoring layer. All inbound web traffic enters through a single intelligent traffic router, which can evaluate each request before deciding whether to send it to real services or into a controlled honeypot path.

The security path is event-driven. The `traffic-router` mirrors request metadata to the `traffic-analyzer`, which applies pattern and behavior rules (SQL injection, XSS, traversal, brute-force, reconnaissance, and directory enumeration). Confirmed detections are published through Redis, where the `deception-controller` consumes them, creates decoy resources, and emits routing updates used by the router.

The key innovation is **dynamic decoy spawning at a 3:1 ratio per attack event**: each qualifying attacker triggers a decoy set of three pods (frontend, API, database). That gives fast attacker containment without permanently overprovisioning honeypots, while TTL-based cleanup and pod caps keep resource usage stable on low-spec hardware.

## 2. Architecture Diagram

```text
                              External Client Traffic
                                      |
                                      v
                        NodePort 30080 (traffic-router)
                                      |
                                      v
+-----------------------------------------------------------------------------------+
| Namespace: deception-gateway (security)                                            |
|                                                                                   |
|  +----------------+     POST /analyze      +-------------------+                 |
|  | traffic-router | ----------------------> | traffic-analyzer  |                 |
|  | OpenResty+Lua  |                         | Flask rules engine|                 |
|  +-------+--------+                         +---------+---------+                 |
|          |                                            |                           |
|          | allow                                      | publish attack_detected   |
|          v                                            v                           |
|  route to real frontend                        Redis pub/sub (monitoring)         |
|          |                                            |                           |
|          | receive routing_update                     | consume attack_detected    |
|          |<-------------------------------------------+                           |
|  +-------+--------+                                                             |
|  | deception-     | -- create/delete decoy pods/services --> decoy-pool         |
|  | controller     | -- publish decoy_spawned + routing_update --> Redis          |
|  +----------------+                                                             |
+-----------------------------------------------------------------------------------+
             |                                            |                     |
             |                                            |                     |
             v                                            v                     v
+-------------------------------+    +--------------------------------+   +------------------------------+
| Namespace: ecommerce-real     |    | Namespace: monitoring          |   | Namespace: decoy-pool        |
|                               |    |                                |   |                              |
| frontend -> product/cart APIs |    | Redis (event bus)             |   | decoy-fe-<id>                |
| product/cart -> postgres      |    | event-collector (WS+REST)     |   | decoy-api-<id>               |
|                               |    | dashboard (NodePort 30088)    |   | decoy-db-<id>                |
+-------------------------------+    +--------------------------------+   +------------------------------+

Data channels:
- attack_detected, decoy_spawned, decoy_interaction, routing_update, pod_status (Redis)
- event-collector -> dashboard via WebSocket (:8090) and REST (:8091)
```

## 3. Prerequisites

### Platform
- Windows 10/11 with **WSL2 enabled**
- Ubuntu 22.04 installed in WSL2
- Minimum hardware:
- CPU: Intel i3 (or equivalent)
- RAM: 4 GB
- Disk: 20 GB free

### Software Requirements
- `git`
- `curl`
- `python3` and `pip`
- Docker Engine running inside WSL (`docker.service`)
- `kubectl`
- Minikube (`--driver=docker`) or K3s
- Bash shell (default in Ubuntu)

### Docker Engine in WSL (after Docker Desktop uninstall)
```bash
# 1) verify Docker Engine service
sudo systemctl status docker --no-pager

# 2) ensure your user can use docker
sudo usermod -aG docker $USER
newgrp docker
docker info
```
If `docker info` still shows `permission denied` on `/var/run/docker.sock`, open a new WSL terminal and rerun.

### Quick Host Checks
```bash
# inside WSL
uname -a
free -h
df -h
kubectl version --client
docker --version
docker info --format '{{.ServerVersion}} {{.OperatingSystem}} {{.Driver}}'
python3 --version
```

## 4. Quick Start

1. **Enable WSL2 and install Ubuntu 22.04**
```powershell
# PowerShell (Admin)
wsl --install -d Ubuntu-22.04
# reboot if prompted
```

2. **Clone this repo**
```bash
git clone <your-repo-url>
cd deception-system
```

3. **Run setup script**
```bash
# setup script for this repo = build + cluster image import
./build-images.sh
```
`build-images.sh` now auto-detects `docker` vs `sudo docker` and imports to Minikube/K3s based on current context.

4. **Run deploy script**
```bash
# skip rebuild because setup already built images
./deploy.sh --skip-build
```

5. **Open dashboard**
- If context is `minikube`, first get node IP:
```bash
NODE_IP=$(kubectl get node minikube -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
echo "$NODE_IP"
```
- Dashboard: `http://<NODE_IP>:30088` (or `http://localhost:30088` on K3s)
- Protected e-commerce entrypoint: `http://<NODE_IP>:30080` (or `http://localhost:30080` on K3s)

6. **Run attack simulator**
```bash
python3 -m pip install -r 07-attack-simulator/requirements.txt
python3 07-attack-simulator/simulate_attacks.py --target http://<NODE_IP>:30080 --attack-type all
```

If `python3 -m pip` is missing:
```bash
sudo apt-get update
sudo apt-get install -y python3-pip
```

## 5. Detailed Component Guide

### 5.1 Namespaces and Policies
- Purpose: isolation between real services, security logic, decoys, and observability.
- How it works: four namespaces (`ecommerce-real`, `deception-gateway`, `decoy-pool`, `monitoring`) plus network policies enforcing segmentation.
- Key config:
- `01-namespaces/namespaces.yaml`
- `01-namespaces/network-policies.yaml`
- `01-namespaces/resource-quotas.yaml`
- Critical rule: `decoy-pool` cannot egress to `ecommerce-real`.

### 5.2 Traffic Router (`traffic-router`)
- Purpose: single ingress and decision point for all HTTP requests.
- How it works: OpenResty/Lua rate-limits (`30 req/s/IP`), checks attacker route cache, calls analyzer for unknown IPs, proxies to real frontend or decoy frontend.
- Key config:
- `03-deception-engine/traffic-router/nginx.conf`
- `03-deception-engine/traffic-router/entrypoint.sh`
- Service exposure: NodePort `30080`.
- Internal debug APIs: `/internal/add-route`, `/internal/remove-route`, `/internal/routes`, `/nginx-health`.

### 5.3 Traffic Analyzer (`traffic-analyzer`)
- Purpose: classify mirrored requests as malicious or benign.
- How it works: Flask API endpoint `POST /analyze` runs `AttackDetector`, filters findings by confidence threshold, publishes `attack_detected` events to Redis.
- Key config:
- `CONFIDENCE_THRESHOLD` (default `0.6`)
- `REDIS_URL`
- `PORT` (default `8085`)
- Files:
- `03-deception-engine/traffic-analyzer/analyzer.py`
- `03-deception-engine/traffic-analyzer/attack_patterns.py`

### 5.4 Deception Controller (`deception-controller`)
- Purpose: orchestrate decoy lifecycle and attacker routing.
- How it works: subscribes to `attack_detected`, creates decoy set resources (3 pods + 3 services), publishes `decoy_spawned` and `routing_update`, deletes expired sets every 60s.
- Key config:
- `MAX_DECOY_PODS=15`, `MAX_DECOY_SETS=5`
- TTL annotation default: `10` minutes
- Eviction policy: if near cap, evict oldest set before spawning new set
- Files:
- `03-deception-engine/deception-controller/controller.py`
- `03-deception-engine/deception-controller/decoy_templates.py`

### 5.5 Decoy Templates (`decoy-pool`)
- Purpose: provide believable but isolated targets.
- How it works: each attack event gets three decoy pods (frontend/API/DB) with labels and annotations for routing, attribution, and cleanup.
- Key config:
- Resource profile per decoy type in `decoy_templates.py`
- Labels: `role=decoy`, `attack-id`, `attacker-ip`, `decoy-type`
- Services are ClusterIP for stable DNS routing.

### 5.6 Real E-commerce Stack (`ecommerce-real`)
- Purpose: legitimate application the system protects.
- How it works:
- `frontend` serves UI and proxies API calls
- `product-service` and `cart-service` provide Flask APIs
- `postgres` stores product/cart data
- Key config files:
- `02-ecommerce-real/frontend/deployment.yaml`
- `02-ecommerce-real/product-service/deployment.yaml`
- `02-ecommerce-real/cart-service/deployment.yaml`
- `02-ecommerce-real/postgres/deployment.yaml`

### 5.7 Redis Event Bus (`monitoring/redis`)
- Purpose: central pub/sub backbone.
- How it works: components publish and subscribe across channels (`attack_detected`, `routing_update`, `decoy_spawned`, etc.).
- Key config:
- Max memory `48MB`, no persistence, pub/sub buffer limits
- File: `05-monitoring/redis/deployment.yaml`

### 5.8 Event Collector (`monitoring/event-collector`)
- Purpose: aggregate Redis and Kubernetes events for visualization.
- How it works: subscribes to Redis, watches pod events via Kubernetes API, builds topology snapshots every 5s, pushes to clients via WebSocket.
- Key config:
- `WEBSOCKET_PORT=8090`
- `REST_PORT=8091`
- `GRAPH_INTERVAL_SECONDS=5`
- `MONITORED_NAMESPACES=ecommerce-real,deception-gateway,decoy-pool,monitoring`
- File: `05-monitoring/event-collector/collector.py`

### 5.9 Dashboard (`monitoring/dashboard`)
- Purpose: live threat operations UI.
- How it works: frontend JS consumes snapshots + events, renders force-directed graph, overlays attack and redirect edges, and updates event feed/stats.
- Key config:
- `EVENT_COLLECTOR_WS`
- `EVENT_COLLECTOR_API`
- NodePort `30088`
- Files:
- `05-monitoring/dashboard/server.js`
- `05-monitoring/dashboard/public/graph.js`

### 5.10 Attack Simulator (`07-attack-simulator`)
- Purpose: reproducible demo traffic (normal + malicious).
- How it works: sends requests with specific attacker IP headers (`X-Forwarded-For`) for SQLi, XSS, traversal, brute-force, and recon scenarios.
- Key config:
- `--target` (default `http://localhost:30080`)
- `--attack-type` (`sqli|xss|traversal|bruteforce|recon|legitimate|all`)
- `--delay` between waves
- Files:
- `07-attack-simulator/simulate_attacks.py`
- `07-attack-simulator/demo-scenario.sh`

## 6. Attack Detection Rules

Detection threshold: findings with confidence **`> 0.6`** trigger deception action.

| Attack Type | Detection Method | Confidence Scoring | System Response |
|---|---|---|---|
| SQL Injection (`sqli`) | Regex signatures over path/query/body/headers (`OR 1=1`, `UNION SELECT`, `DROP`, `SLEEP`, comment evasion, etc.) | Fixed per matched pattern (approx `0.55` to `0.95`) | Publish `attack_detected` -> spawn 3 decoys -> add attacker IP route to decoy frontend |
| XSS (`xss`) | Regex signatures (`<script>`, `javascript:`, event handlers, `eval`, `data:text/html`, etc.) | Fixed per pattern (approx `0.70` to `0.95`) | Same as above |
| Path Traversal (`path_traversal`) | Regex for traversal and sensitive-file probes (`../`, `%2e%2e`, `/etc/passwd`, `windows/system32`, etc.) | Fixed per pattern (approx `0.85` to `0.95`) | Same as above |
| Brute Force (`brute_force`) | Stateful rate detection on auth-like POST endpoints (`/login`, `/auth`, checkout/login paths) | `0.60 + 0.08 * (attempts - threshold)` capped at `0.98` (threshold default 5 attempts / 30s) | Same as above |
| Recon Scanner (`recon_scanner`) | Known scanner User-Agent detection (`sqlmap`, `nikto`, `nmap`, etc.) | Fixed per scanner signature (approx `0.80` to `0.95`) | Same as above |
| Recon Scanning (`recon_scanning`) | Unique path enumeration rate per IP within scan window | `0.65 + 0.05 * (unique_paths - threshold)` capped at `0.98` (default threshold 10 paths / 15s) | Same as above |
| Directory Enumeration (`dir_enum`) | Sensitive/admin endpoint probes (`/admin`, `/.git`, `/wp-login`, `/phpmyadmin`, etc.) | Fixed per path class (approx `0.30` to `0.90`) | Same as above |

## 7. Dashboard Guide

### Visual Node Colors
- **Green nodes** = real services/pods (production app path)
- **Red nodes** = decoys (honeypots in `decoy-pool`)
- **Yellow/orange nodes** = detected attackers (external attacker entities)
- Blue diamond nodes represent gateway/security components.
- Gray nodes represent monitoring services.

### Line Colors and Meanings
- **Red lines** = active attack traffic (`attack_traffic`)
- **Yellow/orange lines** = redirected attacker traffic to decoys (`redirected_traffic`)
- **Green lines** = legitimate service dependencies/traffic (`legitimate_traffic`)
- **Gray lines** = service mesh/internal selector links (`internal_mesh`)

### How to Read the Event Feed
- Each row is timestamped and categorized:
- Attack events (`ATTACK ...`) highlight detection type/IP/confidence.
- Decoy spawn events (`DECOY SPAWN ...`) show attack ID, attacker IP, and number of decoy pods.
- Routing events (`ROUTING add_route/remove_route ...`) indicate redirection rule changes.
- Pod lifecycle events (`POD ADDED/MODIFIED/DELETED ...`) show live infrastructure transitions.
- During a demo, correlate event feed time with node/edge changes to narrate cause -> action -> containment.

## 8. Demo Script

Target duration: ~3 minutes.

### 0:00 - 0:20 (Baseline)
- "Open two browser tabs: one for the e-commerce store (port 30080), one for the dashboard (port 30088)"
- "In a terminal, first show normal traffic flowing..."
```bash
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type legitimate --continuous
```
- Narration: graph shows stable green production path and normal event cadence.

### 0:20 - 0:50 (SQL Injection wave)
- "Now launch the SQL injection attack and narrate what you see..."
```bash
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type sqli
```
- Narration: attack event appears, attacker node becomes active, route updates follow.
- "Point out on the dashboard: the new red decoy nodes appearing..."

### 0:50 - 1:40 (Additional attack waves)
- Run XSS, recon, brute-force, traversal in sequence.
```bash
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type xss
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type recon
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type bruteforce
python3 07-attack-simulator/simulate_attacks.py --target http://localhost:30080 --attack-type traversal
```
- Narration: emphasize repeated 3-pod decoy spawning behavior per qualifying attacker.

### 1:40 - 2:40 (Containment proof)
- Explain that subsequent requests from flagged IPs are redirected to decoys.
- Show `ROUTING add_route` events and redirected (yellow/orange) edges on graph.
- Optionally inspect router route table:
```bash
curl -s http://localhost:30080/internal/routes | python3 -m json.tool
```

### 2:40 - 3:00 (Cleanup behavior)
- Explain TTL cleanup cycle and finite decoy capacity.
- Mention controller sweep interval (60s) and default decoy TTL (10 minutes).
- End with dashboard counters for attacks, spawned decoys, cleaned decoys, and active sets.

For an automated narration flow, use:
```bash
./07-attack-simulator/demo-scenario.sh http://localhost:30080
```

## 9. Troubleshooting

### 9.1 WSL2 memory issues
Symptoms: image builds fail, pods evicted, OOM kills.

Check:
```bash
free -h
kubectl top pods -A  # if metrics server installed
dmesg | tail -100 | rg -i "killed process|oom"
```

Fix (`C:\Users\<you>\.wslconfig` on Windows):
```ini
[wsl2]
memory=6GB
processors=2
swap=4GB
```
Then apply:
```powershell
wsl --shutdown
```

### 9.2 Pods stuck in `Pending` (resource quota exceeded)
Check:
```bash
kubectl describe pod <pod-name> -n <namespace>
kubectl describe resourcequota -n ecommerce-real
kubectl describe resourcequota -n decoy-pool
kubectl describe resourcequota -n monitoring
```
Fix:
- Increase limits in `01-namespaces/resource-quotas.yaml`.
- Re-apply quotas:
```bash
kubectl apply -f 01-namespaces/resource-quotas.yaml
```

### 9.3 Images not found (cluster image import mismatch)
Symptoms: `ImagePullBackOff`, `ErrImageNeverPull`.

Check:
```bash
kubectl describe pod <pod-name> -n <namespace>
kubectl config current-context
```
Fix:
```bash
./build-images.sh
# or build/import a single image
./build-images.sh --image deception/traffic-router
```
If context is `minikube`, verify image exists in minikube runtime:
```bash
minikube image ls | rg deception/
```
If using K3s, verify in `k3s ctr`:
```bash
sudo k3s ctr images list | rg deception/
```
If decoy API/DB images are missing, ensure local images `deception/decoy-api:latest` and `deception/decoy-db:latest` exist.

### 9.4 Network policy blocking legitimate traffic
Check:
```bash
./test-connectivity.sh
kubectl get networkpolicy -A
kubectl describe networkpolicy decoy-pool-egress-deny-real -n decoy-pool
```
Fix:
- Verify service DNS names and ports in policies.
- Ensure only intended `decoy-pool -> ecommerce-real` flows are blocked.
- Re-apply:
```bash
kubectl apply -f 01-namespaces/network-policies.yaml
```

### 9.5 Dashboard not connecting to WebSocket
Check:
```bash
kubectl get pods -n monitoring
kubectl logs -n monitoring deploy/event-collector --tail=100
kubectl logs -n monitoring deploy/dashboard --tail=100
curl -s http://<NODE_IP>:30088/health
```
Fix:
- Confirm `event-collector` is healthy on ports `8090/8091`.
- Verify dashboard env values `EVENT_COLLECTOR_WS` and `EVENT_COLLECTOR_API` in `05-monitoring/dashboard/deployment.yaml`.
- Redeploy monitoring components if needed:
```bash
kubectl rollout restart deploy/event-collector -n monitoring
kubectl rollout restart deploy/dashboard -n monitoring
```

### 9.6 Docker socket permission errors in WSL
Symptoms: `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`.

Check:
```bash
id
ls -l /var/run/docker.sock
sudo systemctl status docker --no-pager
```
Fix:
```bash
sudo usermod -aG docker $USER
newgrp docker
docker info
```
If needed, log out/in to refresh group membership.

## 10. Resource Budget

### Per-Pod Resource Plan

| Pod Type | Namespace | Replicas (baseline/max) | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---|---:|---:|---:|---:|---:|
| `frontend` | `ecommerce-real` | 1 | 50m | 100m | 32Mi | 64Mi |
| `product-service` | `ecommerce-real` | 1 | 75m | 150m | 48Mi | 96Mi |
| `cart-service` | `ecommerce-real` | 1 | 75m | 150m | 48Mi | 96Mi |
| `postgres` | `ecommerce-real` | 1 | 100m | 200m | 64Mi | 128Mi |
| `traffic-router` | `deception-gateway` | 1 | 50m | 100m | 32Mi | 64Mi |
| `traffic-analyzer` | `deception-gateway` | 1 | 75m | 150m | 48Mi | 96Mi |
| `deception-controller` | `deception-gateway` | 1 | 75m | 150m | 48Mi | 96Mi |
| `redis` | `monitoring` | 1 | 50m | 100m | 48Mi | 64Mi |
| `event-collector` | `monitoring` | 1 | 50m | 100m | 48Mi | 96Mi |
| `dashboard` | `monitoring` | 1 | 25m | 50m | 32Mi | 48Mi |
| `decoy-fe-*` | `decoy-pool` | 0 to 4 | 25m | 50m | 32Mi | 48Mi |
| `decoy-api-*` | `decoy-pool` | 0 to 4 | 25m | 50m | 32Mi | 48Mi |
| `decoy-db-*` | `decoy-pool` | 0 to 4 | 50m | 100m | 48Mi | 64Mi |
| `attack-simulator` job (optional) | `default` | 0 or 1 | 25m | 100m | 32Mi | 64Mi |

### Totals

| Scenario | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---:|---:|---:|---:|
| Baseline (no decoys) | 625m | 1250m | 448Mi | 848Mi |
| One attack event (1 decoy set = 3 pods) | 725m | 1450m | 560Mi | 1008Mi |
| Max decoy capacity (5 sets = 15 decoy pods) | 1125m | 2250m | 1008Mi | 1648Mi |

## 11. Cleanup

Tear down all deployed resources:
```bash
./teardown.sh
```

Full cleanup (also removes local Docker images and optionally cluster runtime):
```bash
./teardown.sh --full
```

Quick validation after cleanup:
```bash
kubectl get ns | rg "ecommerce-real|deception-gateway|decoy-pool|monitoring"
```

## 12. Future Enhancements

1. ML-assisted anomaly scoring to complement regex/rule-based detection.
2. Additional decoy service families (SSH, admin panels, fake APIs with deeper interaction scripts).
3. End-to-end TLS with cert automation and mTLS for internal services.
4. SIEM/SOAR integration (Splunk, Elastic, Sentinel) for enterprise alert workflows.
5. Multi-node K3s mode with anti-affinity and HA Redis/event pipeline.
6. Persistent attack intelligence store for long-term analytics and replay.

---

## Useful Commands

```bash
./status.sh
./test-connectivity.sh
./03-deception-engine/update-route.sh list
curl -s http://localhost:30080/nginx-health | python3 -m json.tool
curl -s http://localhost:30088/health | python3 -m json.tool
```
