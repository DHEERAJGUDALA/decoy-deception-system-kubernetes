import asyncio
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis
import websockets
from flask import Flask, jsonify
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException

SERVICE_NAME = "event-collector"
WEBSOCKET_PORT = int(os.environ.get("WEBSOCKET_PORT", "8090"))
REST_PORT = int(os.environ.get("REST_PORT", "8091"))
GRAPH_INTERVAL_SECONDS = int(os.environ.get("GRAPH_INTERVAL_SECONDS", "5"))
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis.monitoring.svc.cluster.local:6379")
MONITORED_NAMESPACES = [
    ns.strip()
    for ns in os.environ.get(
        "MONITORED_NAMESPACES",
        "ecommerce-real,deception-gateway,decoy-pool,monitoring",
    ).split(",")
    if ns.strip()
]

REDIS_CHANNELS = [
    "attack_detected",
    "decoy_spawned",
    "decoy_interaction",
    "routing_update",
    "pod_status",
]

KNOWN_SERVICE_CONNECTIONS = [
    ("ecommerce-real", "frontend", "ecommerce-real", "product-service"),
    ("ecommerce-real", "frontend", "ecommerce-real", "cart-service"),
    ("ecommerce-real", "product-service", "ecommerce-real", "postgres"),
    ("ecommerce-real", "cart-service", "ecommerce-real", "postgres"),
    (
        "deception-gateway",
        "traffic-router",
        "deception-gateway",
        "traffic-analyzer",
    ),
    ("deception-gateway", "traffic-router", "ecommerce-real", "frontend"),
    ("deception-gateway", "traffic-analyzer", "monitoring", "redis"),
    ("deception-gateway", "deception-controller", "monitoring", "redis"),
    ("monitoring", "event-collector", "monitoring", "redis"),
]

MAX_RECENT_EVENTS = 200
LOCAL_EVENT_ID_WINDOW = 2000

app = Flask(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "message": record.getMessage(),
        }
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger = logging.getLogger(SERVICE_NAME)
logger.handlers = [handler]
logger.setLevel(logging.INFO)
logger.propagate = False
logging.getLogger("werkzeug").setLevel(logging.WARNING)

recent_events: deque = deque(maxlen=MAX_RECENT_EVENTS)
recent_events_lock = threading.Lock()

connected_clients: set = set()

event_loop: Optional[asyncio.AbstractEventLoop] = None
event_queue: Optional[asyncio.Queue] = None
loop_ready = threading.Event()

k8s_core: Optional[client.CoreV1Api] = None
k8s_lock = threading.Lock()

redis_publisher: Optional[redis.Redis] = None
redis_publisher_lock = threading.Lock()

local_event_ids: deque = deque()
local_event_id_set: set = set()
local_event_lock = threading.Lock()

attacker_routes: Dict[str, Dict[str, Any]] = {}
attack_id_to_ip: Dict[str, str] = {}
routes_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def service_node_id(namespace: str, name: str) -> str:
    return f"service:{namespace}:{name}"


def pod_node_id(namespace: str, name: str) -> str:
    return f"pod:{namespace}:{name}"


def infer_role(namespace: str, labels: Dict[str, str]) -> str:
    if labels.get("role") == "decoy" or namespace == "decoy-pool":
        return "decoy"
    if namespace == "deception-gateway":
        return "gateway"
    if namespace == "monitoring":
        return "monitoring"
    return "real"


def append_recent_event(event: Dict[str, Any]) -> None:
    with recent_events_lock:
        recent_events.append(event)


def mark_local_event_id(event_id: str) -> None:
    with local_event_lock:
        if event_id in local_event_id_set:
            return
        local_event_ids.append(event_id)
        local_event_id_set.add(event_id)
        while len(local_event_ids) > LOCAL_EVENT_ID_WINDOW:
            old_id = local_event_ids.popleft()
            local_event_id_set.discard(old_id)


def is_local_event_id(event_id: str) -> bool:
    with local_event_lock:
        return event_id in local_event_id_set


def enqueue_event_from_thread(event: Dict[str, Any]) -> None:
    if not loop_ready.is_set() or event_loop is None:
        return
    event_loop.call_soon_threadsafe(_enqueue_event_no_wait, event)


def _enqueue_event_no_wait(event: Dict[str, Any]) -> None:
    if event_queue is None:
        return
    try:
        event_queue.put_nowait(event)
    except Exception as exc:
        logger.warning(f"Failed to queue event: {exc}")


def decode_channel(channel: Any) -> str:
    if isinstance(channel, (bytes, bytearray)):
        return channel.decode("utf-8", errors="replace")
    return str(channel)


def parse_redis_payload(raw_payload: Any) -> Any:
    payload_text = raw_payload
    if isinstance(raw_payload, (bytes, bytearray)):
        payload_text = raw_payload.decode("utf-8", errors="replace")

    if isinstance(payload_text, str):
        try:
            return json.loads(payload_text)
        except json.JSONDecodeError:
            return {"message": payload_text}

    return payload_text


def endpoint_to_service_id(endpoint: Optional[str]) -> Optional[str]:
    if not endpoint:
        return None
    host = endpoint.split(":", 1)[0]
    parts = host.split(".")
    if len(parts) < 2:
        return None
    service_name = parts[0]
    namespace = parts[1]
    return service_node_id(namespace, service_name)


# ---------------------------------------------------------------------------
# Redis and Kubernetes clients
# ---------------------------------------------------------------------------
def get_redis_publisher() -> Optional[redis.Redis]:
    global redis_publisher
    with redis_publisher_lock:
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
            logger.info(f"Redis publisher connected to {REDIS_URL}")
            return redis_publisher
        except redis.RedisError as exc:
            logger.warning(f"Redis publisher unavailable: {exc}")
            redis_publisher = None
            return None


def publish_to_redis(channel: str, event: Dict[str, Any]) -> None:
    global redis_publisher
    redis_client = get_redis_publisher()
    if redis_client is None:
        return

    try:
        redis_client.publish(channel, json.dumps(event))
    except redis.RedisError as exc:
        logger.warning(f"Redis publish to {channel} failed: {exc}")
        with redis_publisher_lock:
            redis_publisher = None


def get_k8s_client() -> Optional[client.CoreV1Api]:
    global k8s_core
    with k8s_lock:
        if k8s_core is not None:
            return k8s_core

        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Loaded local kubeconfig")
            except config.ConfigException as exc:
                logger.error(f"Unable to load Kubernetes config: {exc}")
                return None

        k8s_core = client.CoreV1Api()
        return k8s_core


# ---------------------------------------------------------------------------
# WebSocket broadcast pipeline
# ---------------------------------------------------------------------------
async def websocket_handler(websocket):
    connected_clients.add(websocket)
    logger.info(f"WebSocket client connected. active_clients={len(connected_clients)}")

    try:
        async for _ in websocket:
            continue
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)
        logger.info(
            f"WebSocket client disconnected. active_clients={len(connected_clients)}"
        )


async def _send_to_client(client_socket, payload: str) -> bool:
    try:
        await client_socket.send(payload)
        return True
    except Exception:
        return False


async def broadcast_event(event: Dict[str, Any]) -> None:
    append_recent_event(event)

    if not connected_clients:
        return

    serialized = json.dumps(event, default=str)
    clients = list(connected_clients)
    results = await asyncio.gather(
        *[_send_to_client(client_socket, serialized) for client_socket in clients],
        return_exceptions=False,
    )

    for client_socket, ok in zip(clients, results):
        if not ok:
            connected_clients.discard(client_socket)


async def event_dispatcher_loop() -> None:
    while True:
        if event_queue is None:
            await asyncio.sleep(0.1)
            continue
        event = await event_queue.get()
        await broadcast_event(event)


# ---------------------------------------------------------------------------
# Redis subscription loop
# ---------------------------------------------------------------------------
def update_attacker_routes(event: Dict[str, Any]) -> None:
    event_type = event.get("type") or event.get("event_type")
    attack_id = event.get("attack_id")
    attacker_ip = event.get("attacker_ip")

    if event_type == "add_route":
        frontend_service = event.get("frontend_service")
        if attacker_ip and frontend_service:
            with routes_lock:
                attacker_routes[attacker_ip] = {
                    "target_endpoint": frontend_service,
                    "updated_at": event.get("timestamp", utc_now()),
                    "attack_id": attack_id,
                }
                if attack_id:
                    attack_id_to_ip[attack_id] = attacker_ip

    if event_type == "remove_route":
        with routes_lock:
            if attacker_ip:
                attacker_routes.pop(attacker_ip, None)
            elif attack_id:
                mapped_ip = attack_id_to_ip.get(attack_id)
                if mapped_ip:
                    attacker_routes.pop(mapped_ip, None)

            if attack_id:
                attack_id_to_ip.pop(attack_id, None)


def redis_subscriber_loop() -> None:
    while True:
        try:
            redis_client = redis.from_url(
                REDIS_URL,
                socket_connect_timeout=5,
                socket_timeout=None,
                retry_on_timeout=False,
            )
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(*REDIS_CHANNELS)
            logger.info(f"Subscribed to Redis channels: {', '.join(REDIS_CHANNELS)}")

            for message in pubsub.listen():
                if message.get("type") != "message":
                    continue

                channel = decode_channel(message.get("channel"))
                payload = parse_redis_payload(message.get("data"))

                if isinstance(payload, dict):
                    event = dict(payload)
                else:
                    event = {"message": str(payload)}

                event.setdefault("timestamp", utc_now())
                event.setdefault("channel", channel)
                if "event_type" not in event:
                    event["event_type"] = event.get("type", channel)

                if channel == "routing_update":
                    update_attacker_routes(event)

                event_id = event.get("event_id")
                if isinstance(event_id, str) and is_local_event_id(event_id):
                    continue

                enqueue_event_from_thread(event)

        except redis.RedisError as exc:
            logger.warning(f"Redis subscription error: {exc}; retrying in 5s")
            time.sleep(5)
        except Exception as exc:
            logger.error(f"Unexpected Redis subscriber error: {exc}; retrying in 5s")
            time.sleep(5)


# ---------------------------------------------------------------------------
# Kubernetes pod watch loop
# ---------------------------------------------------------------------------
def build_pod_update_event(k8s_watch_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pod_obj = k8s_watch_event.get("object")
    if pod_obj is None:
        return None

    metadata = pod_obj.metadata
    pod_status = pod_obj.status
    pod_spec = pod_obj.spec

    event_id = str(uuid.uuid4())

    event = {
        "event_id": event_id,
        "event_type": "pod_update",
        "watch_type": k8s_watch_event.get("type", "UNKNOWN"),
        "pod_name": metadata.name,
        "namespace": metadata.namespace,
        "status": getattr(pod_status, "phase", "Unknown"),
        "labels": metadata.labels or {},
        "ip": getattr(pod_status, "pod_ip", None),
        "node": getattr(pod_spec, "node_name", None),
        "timestamp": utc_now(),
        "source": SERVICE_NAME,
    }

    return event


def kubernetes_pod_watch_loop() -> None:
    while True:
        k8s = get_k8s_client()
        if k8s is None:
            time.sleep(5)
            continue

        pod_watch = watch.Watch()
        try:
            for k8s_watch_event in pod_watch.stream(
                k8s.list_pod_for_all_namespaces,
                timeout_seconds=60,
            ):
                event = build_pod_update_event(k8s_watch_event)
                if not event:
                    continue

                event_id = event.get("event_id")
                if isinstance(event_id, str):
                    mark_local_event_id(event_id)

                enqueue_event_from_thread(event)
                publish_to_redis("pod_status", event)

        except ApiException as exc:
            logger.warning(
                f"Kubernetes pod watch API error: {exc.status} {exc.reason}; retrying in 3s"
            )
            time.sleep(3)
        except Exception as exc:
            logger.error(f"Kubernetes pod watch error: {exc}; retrying in 3s")
            time.sleep(3)
        finally:
            pod_watch.stop()


# ---------------------------------------------------------------------------
# Graph snapshot loop
# ---------------------------------------------------------------------------
def add_edge(
    edges: List[Dict[str, Any]],
    edge_keys: set,
    source: str,
    target: str,
    edge_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    key = (source, target, edge_type, json.dumps(extra or {}, sort_keys=True))
    if key in edge_keys:
        return
    edge_keys.add(key)

    edge = {
        "source": source,
        "target": target,
        "type": edge_type,
    }
    if extra:
        edge.update(extra)
    edges.append(edge)


def build_graph_snapshot_event() -> Dict[str, Any]:
    k8s = get_k8s_client()
    if k8s is None:
        return {
            "event_type": "graph_snapshot",
            "timestamp": utc_now(),
            "nodes": [],
            "edges": [],
            "error": "kubernetes_unavailable",
        }

    all_pods: List[Any] = []
    all_services: List[Any] = []
    pods_by_namespace: Dict[str, List[Any]] = {}

    for namespace in MONITORED_NAMESPACES:
        try:
            pods = k8s.list_namespaced_pod(namespace=namespace).items
            services = k8s.list_namespaced_service(namespace=namespace).items
        except ApiException as exc:
            logger.warning(
                f"Snapshot list error ns={namespace}: {exc.status} {exc.reason}"
            )
            continue

        pods_by_namespace[namespace] = pods
        all_pods.extend(pods)
        all_services.extend(services)

    nodes: List[Dict[str, Any]] = []
    node_index: Dict[str, Dict[str, Any]] = {}

    for pod in all_pods:
        metadata = pod.metadata
        labels = metadata.labels or {}
        node = {
            "id": pod_node_id(metadata.namespace, metadata.name),
            "name": metadata.name,
            "namespace": metadata.namespace,
            "type": "pod",
            "role": infer_role(metadata.namespace, labels),
            "status": getattr(pod.status, "phase", "Unknown"),
            "labels": labels,
        }
        nodes.append(node)
        node_index[node["id"]] = node

    for service in all_services:
        metadata = service.metadata
        labels = metadata.labels or {}
        node = {
            "id": service_node_id(metadata.namespace, metadata.name),
            "name": metadata.name,
            "namespace": metadata.namespace,
            "type": "service",
            "role": infer_role(metadata.namespace, labels),
            "status": "Active" if not metadata.deletion_timestamp else "Terminating",
            "labels": labels,
        }
        nodes.append(node)
        node_index[node["id"]] = node

    edges: List[Dict[str, Any]] = []
    edge_keys = set()

    for service in all_services:
        namespace = service.metadata.namespace
        selector = service.spec.selector or {}
        if not selector:
            continue

        svc_id = service_node_id(namespace, service.metadata.name)
        namespace_pods = pods_by_namespace.get(namespace, [])

        for pod in namespace_pods:
            pod_labels = pod.metadata.labels or {}
            if all(pod_labels.get(k) == v for k, v in selector.items()):
                pod_id = pod_node_id(namespace, pod.metadata.name)
                add_edge(
                    edges,
                    edge_keys,
                    svc_id,
                    pod_id,
                    "service_selector",
                )

    for src_ns, src_name, dst_ns, dst_name in KNOWN_SERVICE_CONNECTIONS:
        src_id = service_node_id(src_ns, src_name)
        dst_id = service_node_id(dst_ns, dst_name)
        if src_id in node_index and dst_id in node_index:
            add_edge(edges, edge_keys, src_id, dst_id, "service_dependency")

    with routes_lock:
        routes_snapshot = dict(attacker_routes)

    router_node = service_node_id("deception-gateway", "traffic-router")
    for attacker_ip, route_data in routes_snapshot.items():
        target_id = endpoint_to_service_id(route_data.get("target_endpoint"))
        if (
            target_id
            and router_node in node_index
            and target_id in node_index
        ):
            add_edge(
                edges,
                edge_keys,
                router_node,
                target_id,
                "attacker_route",
                {"attacker_ip": attacker_ip},
            )

    return {
        "event_type": "graph_snapshot",
        "timestamp": utc_now(),
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "namespaces": MONITORED_NAMESPACES,
            "pod_count": len(all_pods),
            "service_count": len(all_services),
        },
    }


def graph_snapshot_loop() -> None:
    while True:
        started = time.monotonic()
        try:
            snapshot_event = build_graph_snapshot_event()
            enqueue_event_from_thread(snapshot_event)
        except Exception as exc:
            logger.error(f"Graph snapshot loop error: {exc}")

        elapsed = time.monotonic() - started
        delay = max(1.0, GRAPH_INTERVAL_SECONDS - elapsed)
        time.sleep(delay)


# ---------------------------------------------------------------------------
# Flask REST API
# ---------------------------------------------------------------------------
@app.route("/api/events/recent", methods=["GET"])
def get_recent_events():
    with recent_events_lock:
        events = list(recent_events)

    return jsonify(
        {
            "service": SERVICE_NAME,
            "count": len(events),
            "events": events,
        }
    )


@app.route("/health", methods=["GET"])
def health():
    with recent_events_lock:
        recent_count = len(recent_events)

    return (
        jsonify(
            {
                "status": "ok",
                "service": SERVICE_NAME,
                "websocket_port": WEBSOCKET_PORT,
                "rest_port": REST_PORT,
                "connected_clients": len(connected_clients),
                "recent_events": recent_count,
            }
        ),
        200,
    )


def run_rest_server() -> None:
    logger.info(f"Starting REST server on 0.0.0.0:{REST_PORT}")
    app.run(host="0.0.0.0", port=REST_PORT, debug=False, threaded=True, use_reloader=False)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def start_background_threads() -> None:
    thread_specs = [
        ("rest-server", run_rest_server),
        ("redis-subscriber", redis_subscriber_loop),
        ("k8s-pod-watch", kubernetes_pod_watch_loop),
        ("graph-snapshot", graph_snapshot_loop),
    ]

    for name, target in thread_specs:
        thread = threading.Thread(name=name, target=target, daemon=True)
        thread.start()


async def async_main() -> None:
    global event_loop, event_queue
    event_loop = asyncio.get_running_loop()
    event_queue = asyncio.Queue()
    loop_ready.set()

    start_background_threads()
    dispatcher_task = asyncio.create_task(event_dispatcher_loop())

    logger.info(f"Starting WebSocket server on 0.0.0.0:{WEBSOCKET_PORT}")
    async with websockets.serve(websocket_handler, "0.0.0.0", WEBSOCKET_PORT):
        await asyncio.Future()

    await dispatcher_task


def main() -> None:
    logger.info(
        "Starting event collector "
        f"(websocket={WEBSOCKET_PORT}, rest={REST_PORT}, namespaces={MONITORED_NAMESPACES})"
    )
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
