"""
Deception Controller — Kubernetes operator for dynamic honeypot management.

The core brain of the deception system. Subscribes to Redis for attack
events, dynamically spawns decoy pod sets in the decoy-pool namespace,
manages their lifecycle with TTL-based cleanup, and enforces the 15-pod
resource cap.

Architecture:
    - Main thread: Flask HTTP server (status/health endpoints)
    - Thread 1: Redis subscriber listening on "attack_detected" channel
    - Thread 2: TTL cleanup loop running every 60 seconds

Uses the official kubernetes Python client with in-cluster ServiceAccount
authentication (deception-controller SA from rbac.yaml).
"""

import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import redis
from flask import Flask, g, jsonify, request
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from decoy_templates import create_decoy_set

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Structured JSON logging to stdout
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": "deception-controller",
            "message": record.getMessage(),
        }
        return json.dumps(log_record)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Also configure the root logger for non-Flask code paths
root_logger = logging.getLogger("controller")
root_logger.handlers = [handler]
root_logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get(
    "REDIS_URL", "redis://redis.monitoring.svc.cluster.local:6379"
)
DECOY_NAMESPACE = os.environ.get("DECOY_NAMESPACE", "decoy-pool")
PORT = int(os.environ.get("PORT", "8086"))

# Channel names
CH_ATTACK_DETECTED = "attack_detected"
CH_DECOY_SPAWNED = "decoy_spawned"
CH_ROUTING_UPDATE = "routing_update"

# Limits
MAX_DECOY_PODS = 15  # matches decoy-pool ResourceQuota
MAX_DECOY_SETS = 5  # 15 pods / 3 pods per set
POD_READY_TIMEOUT = 120  # seconds to wait for pods to become Ready
TTL_CHECK_INTERVAL = 60  # seconds between TTL cleanup sweeps

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
controller_stats = {
    "total_spawned_sets": 0,
    "total_cleaned_sets": 0,
    "total_attacks_received": 0,
    "total_duplicate_skipped": 0,
    "total_evictions": 0,
    "started_at": datetime.now(timezone.utc).isoformat(),
    "active_decoy_sets": {},  # attack_id_short -> {attacker_ip, attack_type, created_at, pods: [...]}
}
stats_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Kubernetes client
# ---------------------------------------------------------------------------
k8s_core = None


def get_k8s_client():
    """
    Initialise the Kubernetes CoreV1Api client.

    Uses in-cluster config when running inside a pod (ServiceAccount token),
    falls back to kubeconfig for local development.
    """
    global k8s_core
    if k8s_core is not None:
        return k8s_core
    try:
        config.load_incluster_config()
        root_logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        try:
            config.load_kube_config()
            root_logger.info("Loaded kubeconfig (local dev mode)")
        except config.ConfigException:
            root_logger.error("Cannot load Kubernetes config")
            return None
    k8s_core = client.CoreV1Api()
    return k8s_core


# ---------------------------------------------------------------------------
# Redis clients — separate for pub/sub (subscriber) and publishing
# ---------------------------------------------------------------------------
redis_publisher = None
redis_subscriber = None


def get_redis_publisher():
    """Get or create the Redis client used for publishing events."""
    global redis_publisher
    if redis_publisher is not None:
        return redis_publisher
    try:
        redis_publisher = redis.from_url(
            REDIS_URL,
            socket_connect_timeout=5,
            socket_timeout=3,
            retry_on_timeout=False,
        )
        redis_publisher.ping()
        root_logger.info(f"Redis publisher connected to {REDIS_URL}")
        return redis_publisher
    except redis.RedisError as e:
        root_logger.warning(f"Redis publisher unavailable: {e}")
        redis_publisher = None
        return None


def publish_event(channel, event):
    """Publish a JSON event to a Redis channel. Fails silently."""
    client = get_redis_publisher()
    if client is None:
        return
    try:
        client.publish(channel, json.dumps(event))
    except redis.RedisError as e:
        root_logger.warning(f"Redis publish to {channel} failed: {e}")
        global redis_publisher
        redis_publisher = None


# ============================================================================
# Core: Decoy spawning logic
# ============================================================================


def _get_active_attack_ids():
    """Return set of short attack-ids that currently have pods in decoy-pool."""
    k8s = get_k8s_client()
    if k8s is None:
        return set()
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector="role=decoy",
        )
        ids = set()
        for pod in pods.items:
            aid = pod.metadata.labels.get("attack-id", "")
            if aid:
                ids.add(aid)
        return ids
    except ApiException as e:
        root_logger.error(f"Failed to list pods: {e.status} {e.reason}")
        return set()


def _get_decoy_pod_count():
    """Return the current number of decoy pods in decoy-pool."""
    k8s = get_k8s_client()
    if k8s is None:
        return 0
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector="role=decoy",
        )
        return len(pods.items)
    except ApiException as e:
        root_logger.error(f"Failed to count pods: {e.status} {e.reason}")
        return 0


def _has_existing_decoys_for_ip(attacker_ip):
    """Check if decoys already exist for a given attacker IP."""
    safe_ip = attacker_ip.replace(":", "-")
    k8s = get_k8s_client()
    if k8s is None:
        return False
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector=f"role=decoy,attacker-ip={safe_ip}",
        )
        return len(pods.items) > 0
    except ApiException as e:
        root_logger.error(f"Failed to check existing decoys: {e.status} {e.reason}")
        return False


def _get_existing_attack_short_for_ip(attacker_ip):
    """Return existing short attack-id for a given attacker IP, or None."""
    safe_ip = attacker_ip.replace(":", "-")
    k8s = get_k8s_client()
    if k8s is None:
        return None
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector=f"role=decoy,attacker-ip={safe_ip}",
        )
        for pod in pods.items:
            attack_short = (pod.metadata.labels or {}).get("attack-id", "")
            if attack_short:
                return attack_short
    except ApiException as e:
        root_logger.error(
            f"Failed to resolve attack-id for IP {attacker_ip}: {e.status}"
        )
    return None


def _is_attack_set_ready(attack_id_short):
    """Return True when all pods in a decoy set report Ready=True."""
    if not attack_id_short:
        return False
    k8s = get_k8s_client()
    if k8s is None:
        return False
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector=f"role=decoy,attack-id={attack_id_short}",
        )
        if not pods.items:
            return False
        for pod in pods.items:
            if pod.status.phase != "Running":
                return False
            conditions = pod.status.conditions or []
            ready_cond = next((c for c in conditions if c.type == "Ready"), None)
            if ready_cond is None or ready_cond.status != "True":
                return False
        return True
    except ApiException as e:
        root_logger.error(
            f"Failed readiness check for attack-id {attack_id_short}: {e.status}"
        )
        return False


def _find_oldest_attack_set():
    """
    Find the oldest decoy set by created-at annotation.

    Returns the short attack-id of the oldest set, or None.
    """
    k8s = get_k8s_client()
    if k8s is None:
        return None
    try:
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector="role=decoy",
        )
        # Group by attack-id, find the oldest created-at
        sets = {}
        for pod in pods.items:
            aid = pod.metadata.labels.get("attack-id", "")
            created = pod.metadata.annotations.get("deception-system/created-at", "")
            if aid and created:
                if aid not in sets or created < sets[aid]:
                    sets[aid] = created
        if not sets:
            return None
        # Return the attack-id with the earliest created-at
        return min(sets, key=sets.get)
    except ApiException as e:
        root_logger.error(f"Failed to find oldest set: {e.status} {e.reason}")
        return None


def _delete_decoy_set(attack_id_short):
    """
    Delete all pods and services for a given attack-id.

    Returns the count of resources deleted.
    """
    k8s = get_k8s_client()
    if k8s is None:
        return 0
    deleted = 0
    label_sel = f"role=decoy,attack-id={attack_id_short}"

    try:
        # Delete pods
        pods = k8s.list_namespaced_pod(
            namespace=DECOY_NAMESPACE,
            label_selector=label_sel,
        )
        for pod in pods.items:
            try:
                k8s.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace=DECOY_NAMESPACE,
                )
                root_logger.info(
                    f"Deleted pod {pod.metadata.name} (attack-id={attack_id_short})"
                )
                deleted += 1
            except ApiException as e:
                root_logger.warning(
                    f"Failed to delete pod {pod.metadata.name}: {e.status}"
                )

        # Delete services
        services = k8s.list_namespaced_service(
            namespace=DECOY_NAMESPACE,
            label_selector=label_sel,
        )
        for svc in services.items:
            try:
                k8s.delete_namespaced_service(
                    name=svc.metadata.name,
                    namespace=DECOY_NAMESPACE,
                )
                root_logger.info(
                    f"Deleted service {svc.metadata.name} (attack-id={attack_id_short})"
                )
                deleted += 1
            except ApiException as e:
                root_logger.warning(
                    f"Failed to delete service {svc.metadata.name}: {e.status}"
                )

    except ApiException as e:
        root_logger.error(f"Failed to list resources for deletion: {e.status}")

    # Remove from tracking state
    with stats_lock:
        controller_stats["active_decoy_sets"].pop(attack_id_short, None)

    return deleted


def _wait_for_pods_running(pod_names, timeout=POD_READY_TIMEOUT):
    """
    Poll until all named pods are Ready (running + containers ready).

    Returns True if all pods are Ready, False on timeout.
    """
    k8s = get_k8s_client()
    if k8s is None:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        all_running = True
        for name in pod_names:
            try:
                pod = k8s.read_namespaced_pod(
                    name=name,
                    namespace=DECOY_NAMESPACE,
                )
                if pod.status.phase != "Running":
                    all_running = False
                    break

                conditions = pod.status.conditions or []
                ready_cond = next((c for c in conditions if c.type == "Ready"), None)
                if ready_cond is None or ready_cond.status != "True":
                    all_running = False
                    break
            except ApiException:
                all_running = False
                break

        if all_running:
            return True
        time.sleep(2)

    return False


def handle_attack_event(event_data):
    """
    Process an attack_detected event: spawn decoys if appropriate.

    This is the main orchestration function called by the Redis subscriber.
    """
    source_ip = event_data.get("source_ip", "unknown")
    attack_type = event_data.get("attack_type", "unknown")
    # Generate a unique attack ID for this decoy set
    attack_id = event_data.get("attack_id", str(uuid.uuid4()))

    with stats_lock:
        controller_stats["total_attacks_received"] += 1

    root_logger.info(
        f"Attack event: type={attack_type} ip={source_ip} id={attack_id[:8]}"
    )

    # --- Check for duplicate: already have decoys for this IP ---
    if _has_existing_decoys_for_ip(source_ip):
        root_logger.info(f"Decoys already exist for IP {source_ip}, skipping")
        existing_short = _get_existing_attack_short_for_ip(source_ip)
        if existing_short and _is_attack_set_ready(existing_short):
            # Re-publish route in case router restarted or initial route publish was skipped.
            publish_event(
                CH_ROUTING_UPDATE,
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "add_route",
                    "attacker_ip": source_ip,
                    "attack_id": existing_short,
                    "frontend_service": f"decoy-fe-{existing_short}.{DECOY_NAMESPACE}.svc.cluster.local:3000",
                    "api_service": f"decoy-api-{existing_short}.{DECOY_NAMESPACE}.svc.cluster.local:8081",
                    "db_service": f"decoy-db-{existing_short}.{DECOY_NAMESPACE}.svc.cluster.local:5432",
                },
            )
            root_logger.info(
                f"Re-published route for existing decoys: ip={source_ip} attack={existing_short}"
            )
        with stats_lock:
            controller_stats["total_duplicate_skipped"] += 1
        return

    # --- Resource guard: evict oldest if at capacity ---
    current_count = _get_decoy_pod_count()
    if current_count >= MAX_DECOY_PODS - 2:
        # Need room for 3 new pods; evict the oldest set
        oldest = _find_oldest_attack_set()
        if oldest:
            root_logger.info(
                f"At capacity ({current_count} pods), evicting oldest set: {oldest}"
            )
            _delete_decoy_set(oldest)
            publish_event(
                CH_DECOY_SPAWNED,
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "decoy_evicted",
                    "attack_id": oldest,
                    "reason": "capacity_limit",
                },
            )
            with stats_lock:
                controller_stats["total_evictions"] += 1
                controller_stats["total_cleaned_sets"] += 1

    # --- Generate decoy resources ---
    resources = create_decoy_set(attack_id, source_ip, attack_type)

    # --- Apply to cluster ---
    k8s = get_k8s_client()
    if k8s is None:
        root_logger.error("Kubernetes client unavailable, cannot spawn decoys")
        return

    created_pods = []
    created_services = []
    quota_failure = False

    for resource in resources:
        kind = resource.get("kind", "")
        name = resource["metadata"]["name"]

        try:
            if kind == "Pod":
                k8s.create_namespaced_pod(
                    namespace=DECOY_NAMESPACE,
                    body=resource,
                )
                created_pods.append(name)
                root_logger.info(f"Created pod: {name}")
            elif kind == "Service":
                k8s.create_namespaced_service(
                    namespace=DECOY_NAMESPACE,
                    body=resource,
                )
                created_services.append(name)
                root_logger.info(f"Created service: {name}")
        except ApiException as e:
            root_logger.error(
                f"Failed to create {kind} {name}: {e.status} {e.reason} — {e.body}"
            )
            if e.status == 403 and "exceeded quota" in str(e.body):
                quota_failure = True

    if quota_failure and len(created_pods) < 3:
        root_logger.warning(
            f"Partial creation due to quota ({len(created_pods)} pods), cleaning up attack {attack_id[:8]}"
        )
        _delete_decoy_set(attack_id[:8])
        return

    if not created_pods:
        root_logger.error("No pods were created, aborting decoy set")
        return

    # --- Wait for pods to be Ready ---
    root_logger.info(f"Waiting for {len(created_pods)} pods to reach Ready state...")
    pods_ready = _wait_for_pods_running(created_pods, timeout=POD_READY_TIMEOUT)
    if pods_ready:
        root_logger.info(f"All decoy pods ready for attack {attack_id[:8]}")
    else:
        root_logger.warning(
            f"Timeout: not all decoy pods Ready for attack {attack_id[:8]} "
            f"(skipping route update to avoid 502s)"
        )

    # --- Update tracking state ---
    short_id = attack_id[:8]
    with stats_lock:
        controller_stats["total_spawned_sets"] += 1
        controller_stats["active_decoy_sets"][short_id] = {
            "attack_id": attack_id,
            "attacker_ip": source_ip,
            "attack_type": attack_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pods": created_pods,
            "services": created_services,
            "pods_ready": pods_ready,
        }

    # --- Publish decoy_spawned event ---
    publish_event(
        CH_DECOY_SPAWNED,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "decoy_spawned",
            "attack_id": attack_id,
            "attacker_ip": source_ip,
            "attack_type": attack_type,
            "decoy_pods": created_pods,
            "decoy_services": created_services,
            "pods_ready": pods_ready,
        },
    )

    # --- Notify traffic-router to redirect this IP only when decoys are Ready ---
    if pods_ready:
        publish_event(
            CH_ROUTING_UPDATE,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "add_route",
                "attacker_ip": source_ip,
                "attack_id": attack_id,
                "frontend_service": f"decoy-fe-{short_id}.{DECOY_NAMESPACE}.svc.cluster.local:3000",
                "api_service": f"decoy-api-{short_id}.{DECOY_NAMESPACE}.svc.cluster.local:8081",
                "db_service": f"decoy-db-{short_id}.{DECOY_NAMESPACE}.svc.cluster.local:5432",
            },
        )

    root_logger.info(
        f"Decoy set complete: attack={short_id} ip={source_ip} "
        f"pods={created_pods} services={created_services}"
    )


# ============================================================================
# TTL cleanup loop
# ============================================================================


def _ttl_cleanup():
    """
    Check all decoy pods for TTL expiry and delete expired sets.

    Runs every TTL_CHECK_INTERVAL seconds in a background thread.
    """
    while True:
        time.sleep(TTL_CHECK_INTERVAL)
        try:
            k8s = get_k8s_client()
            if k8s is None:
                continue

            pods = k8s.list_namespaced_pod(
                namespace=DECOY_NAMESPACE,
                label_selector="role=decoy",
            )

            now = datetime.now(timezone.utc)
            expired_sets = set()

            for pod in pods.items:
                annotations = pod.metadata.annotations or {}
                created_str = annotations.get("deception-system/created-at", "")
                ttl_str = annotations.get("deception-system/ttl-minutes", "10")
                attack_id_short = pod.metadata.labels.get("attack-id", "")

                if not created_str or not attack_id_short:
                    continue

                try:
                    created_at = datetime.fromisoformat(created_str)
                    ttl_minutes = int(ttl_str)
                    age_minutes = (now - created_at).total_seconds() / 60.0

                    if age_minutes > ttl_minutes:
                        expired_sets.add(attack_id_short)
                except (ValueError, TypeError):
                    continue

            # Delete each expired set
            for attack_id_short in expired_sets:
                root_logger.info(
                    f"TTL expired for attack set {attack_id_short}, cleaning up"
                )
                deleted = _delete_decoy_set(attack_id_short)

                # Publish deletion event
                publish_event(
                    CH_DECOY_SPAWNED,
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "type": "decoy_expired",
                        "attack_id": attack_id_short,
                        "resources_deleted": deleted,
                        "reason": "ttl_expired",
                    },
                )

                # Notify traffic-router to remove routing rule
                publish_event(
                    CH_ROUTING_UPDATE,
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "type": "remove_route",
                        "attack_id": attack_id_short,
                        "reason": "ttl_expired",
                    },
                )

                with stats_lock:
                    controller_stats["total_cleaned_sets"] += 1

        except Exception as e:
            root_logger.error(f"TTL cleanup error: {e}")


# ============================================================================
# Redis subscriber loop
# ============================================================================


def _redis_subscriber_loop():
    """
    Subscribe to the attack_detected Redis channel and process events.

    Reconnects automatically if the connection drops.
    """
    while True:
        try:
            sub_client = redis.from_url(
                REDIS_URL,
                socket_connect_timeout=5,
                socket_timeout=None,  # blocking subscribe
            )
            pubsub = sub_client.pubsub()
            pubsub.subscribe(CH_ATTACK_DETECTED)
            root_logger.info(f"Subscribed to Redis channel '{CH_ATTACK_DETECTED}'")

            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    handle_attack_event(data)
                except json.JSONDecodeError as e:
                    root_logger.warning(f"Invalid JSON from Redis: {e}")
                except Exception as e:
                    root_logger.error(f"Error handling attack event: {e}")

        except redis.RedisError as e:
            root_logger.warning(
                f"Redis subscriber disconnected: {e}, reconnecting in 5s..."
            )
            time.sleep(5)
        except Exception as e:
            root_logger.error(f"Subscriber loop error: {e}, retrying in 5s...")
            time.sleep(5)


# ============================================================================
# Flask endpoints
# ============================================================================


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.before_request
def start_timer():
    g.start_time = time.monotonic()


@app.after_request
def after_request(response):
    response.headers["X-Service-Node"] = "deception-controller"
    duration_ms = round(
        (time.monotonic() - g.get("start_time", time.monotonic())) * 1000, 2
    )
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": request.method,
        "path": request.path,
        "source_ip": request.remote_addr,
        "response_code": response.status_code,
        "duration_ms": duration_ms,
    }
    app.logger.info(json.dumps(log_entry))
    return response


# ---------------------------------------------------------------------------
# GET /status — Current controller state
# ---------------------------------------------------------------------------
@app.route("/status")
def status():
    """Return current decoy controller state."""
    pod_count = _get_decoy_pod_count()
    with stats_lock:
        return jsonify(
            {
                "total_attacks_received": controller_stats["total_attacks_received"],
                "total_spawned_sets": controller_stats["total_spawned_sets"],
                "total_cleaned_sets": controller_stats["total_cleaned_sets"],
                "total_duplicate_skipped": controller_stats["total_duplicate_skipped"],
                "total_evictions": controller_stats["total_evictions"],
                "active_decoy_sets": controller_stats["active_decoy_sets"],
                "active_set_count": len(controller_stats["active_decoy_sets"]),
                "current_pod_count": pod_count,
                "max_pods": MAX_DECOY_PODS,
                "max_sets": MAX_DECOY_SETS,
                "decoy_namespace": DECOY_NAMESPACE,
                "started_at": controller_stats["started_at"],
                "uptime_seconds": round(
                    (
                        datetime.now(timezone.utc)
                        - datetime.fromisoformat(controller_stats["started_at"])
                    ).total_seconds()
                ),
            }
        )


# ---------------------------------------------------------------------------
# GET /health — Health check
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    """Health check for Kubernetes probes."""
    k8s_ok = get_k8s_client() is not None
    redis_ok = False
    try:
        pub = get_redis_publisher()
        if pub:
            pub.ping()
            redis_ok = True
    except Exception:
        pass

    return jsonify(
        {
            "status": "healthy",
            "service": "deception-controller",
            "kubernetes_connected": k8s_ok,
            "redis_connected": redis_ok,
        }
    )


# ============================================================================
# Startup: launch background threads
# ============================================================================


def start_background_threads():
    """Launch the Redis subscriber and TTL cleanup threads."""
    subscriber_thread = threading.Thread(
        target=_redis_subscriber_loop,
        daemon=True,
        name="redis-subscriber",
    )
    subscriber_thread.start()
    root_logger.info("Redis subscriber thread started")

    ttl_thread = threading.Thread(
        target=_ttl_cleanup,
        daemon=True,
        name="ttl-cleanup",
    )
    ttl_thread.start()
    root_logger.info("TTL cleanup thread started")


# Start background threads at import time (gunicorn --preload will call this
# once in the master process, and the daemon threads will be inherited by
# forked workers). For single-worker mode this is straightforward.
start_background_threads()


# ---------------------------------------------------------------------------
# Dev server
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
