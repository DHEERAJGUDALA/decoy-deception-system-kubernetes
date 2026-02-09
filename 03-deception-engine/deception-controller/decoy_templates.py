"""
Decoy Templates — Kubernetes resource definitions for honeypot pod sets.

Generates Pod and Service specifications as kubernetes client model dicts
for dynamic creation by the deception controller. Each "decoy set" consists
of 3 pods (frontend, API, database) that together mimic the real e-commerce
stack but serve fake data and log all attacker interactions.

All resources are created in the decoy-pool namespace with labels that
tie them to a specific attack event, enabling bulk cleanup by attack-id.
"""

import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DECOY_NAMESPACE = os.environ.get("DECOY_NAMESPACE", "decoy-pool")
REDIS_URL = os.environ.get(
    "REDIS_URL", "redis://redis.monitoring.svc.cluster.local:6379"
)
DEFAULT_TTL_MINUTES = int(os.environ.get("DECOY_TTL_MINUTES", "10"))


# ============================================================================
# Public API
# ============================================================================


def create_decoy_set(attack_id, attacker_ip, attack_type):
    """
    Generate a complete decoy set (3 pods + 3 services) for an attack event.

    Parameters
    ----------
    attack_id : str
        Unique identifier for the attack event (UUID or similar).
    attacker_ip : str
        Source IP of the attacker.
    attack_type : str
        Classification of the attack (e.g. "sqli", "xss", "recon_scanning").

    Returns
    -------
    list[dict]
        List of 6 Kubernetes resource dicts (3 pods, 3 services), each with
        a "kind" field ("Pod" or "Service") for the controller to dispatch.
    """
    short_id = attack_id[:8]
    now = datetime.now(timezone.utc).isoformat()

    # Sanitise attacker_ip for use in label values (dots are allowed, colons
    # from IPv6 are not — replace with dashes)
    safe_ip = attacker_ip.replace(":", "-")

    resources = []

    # --- Decoy Frontend (port 3000) ---
    fe_name = f"decoy-fe-{short_id}"
    resources.append(
        _make_pod(
            name=fe_name,
            image="deception/decoy-frontend:latest",
            port=3000,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            attack_type=attack_type,
            decoy_type="frontend",
            created_at=now,
            resources_limits={"memory": "96Mi", "cpu": "50m"},
            resources_requests={"memory": "32Mi", "cpu": "25m"},
            env_extra=[],
        )
    )
    resources.append(
        _make_service(
            name=fe_name,
            port=3000,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            decoy_type="frontend",
        )
    )

    # --- Decoy API (port 8081) ---
    api_name = f"decoy-api-{short_id}"
    resources.append(
        _make_pod(
            name=api_name,
            image="deception/decoy-api:latest",
            port=8081,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            attack_type=attack_type,
            decoy_type="api",
            created_at=now,
            resources_limits={"memory": "96Mi", "cpu": "50m"},
            resources_requests={"memory": "32Mi", "cpu": "25m"},
            env_extra=[],
        )
    )
    resources.append(
        _make_service(
            name=api_name,
            port=8081,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            decoy_type="api",
        )
    )

    # --- Decoy DB (port 5432) ---
    db_name = f"decoy-db-{short_id}"
    resources.append(
        _make_pod(
            name=db_name,
            image="deception/decoy-db:latest",
            port=5432,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            attack_type=attack_type,
            decoy_type="database",
            created_at=now,
            # DB gets more resources — postgres overhead
            resources_limits={"memory": "64Mi", "cpu": "100m"},
            resources_requests={"memory": "48Mi", "cpu": "50m"},
            env_extra=[
                {"name": "POSTGRES_DB", "value": "ecommerce"},
                {"name": "POSTGRES_USER", "value": "appuser"},
                {"name": "POSTGRES_PASSWORD", "value": "d3c0y-Tr4p-2024"},
            ],
        )
    )
    resources.append(
        _make_service(
            name=db_name,
            port=5432,
            attack_id=attack_id,
            attacker_ip=safe_ip,
            decoy_type="database",
        )
    )

    return resources


# ============================================================================
# Private helpers — build K8s resource dicts
# ============================================================================


def _make_pod(
    name,
    image,
    port,
    attack_id,
    attacker_ip,
    attack_type,
    decoy_type,
    created_at,
    resources_limits,
    resources_requests,
    env_extra,
):
    """
    Build a Pod spec dict compatible with kubernetes.client.CoreV1Api.

    Uses plain dicts rather than kubernetes client model objects so the
    controller can serialise/log them easily and pass them to
    create_namespaced_pod(body=...).
    """
    short_id = attack_id[:8]

    labels = {
        "app": name,
        "role": "decoy",
        "attack-id": short_id,
        "decoy-type": decoy_type,
        "attacker-ip": attacker_ip,
        "app.kubernetes.io/part-of": "deception-system",
        "app.kubernetes.io/component": f"decoy-{decoy_type}",
    }

    annotations = {
        "deception-system/created-at": created_at,
        "deception-system/attack-type": attack_type,
        "deception-system/ttl-minutes": str(DEFAULT_TTL_MINUTES),
        "deception-system/attack-id": attack_id,
        "deception-system/attacker-ip": attacker_ip,
    }

    # Standard env vars for all decoy pods
    env = [
        {"name": "DECOY_ID", "value": f"{decoy_type}-{short_id}"},
        {"name": "ATTACK_ID", "value": attack_id},
        {"name": "ATTACKER_IP", "value": attacker_ip},
        {"name": "REDIS_URL", "value": REDIS_URL},
    ] + env_extra

    probe = None
    startup_probe = None
    if decoy_type in {"frontend", "api"}:
        probe = {
            "httpGet": {"path": "/health", "port": port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "timeoutSeconds": 2,
            "failureThreshold": 6,
        }
        startup_probe = {
            "httpGet": {"path": "/health", "port": port},
            "periodSeconds": 2,
            "timeoutSeconds": 2,
            "failureThreshold": 45,
        }
    elif decoy_type == "database":
        probe = {
            "tcpSocket": {"port": port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "timeoutSeconds": 2,
            "failureThreshold": 6,
        }

    container_spec = {
        "name": name,
        "image": image,
        "imagePullPolicy": "Never",
        "ports": [{"containerPort": port, "protocol": "TCP"}],
        "env": env,
        "resources": {
            "requests": resources_requests,
            "limits": resources_limits,
        },
    }
    if probe:
        container_spec["readinessProbe"] = probe
        container_spec["livenessProbe"] = probe
    if startup_probe:
        container_spec["startupProbe"] = startup_probe

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": DECOY_NAMESPACE,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "restartPolicy": "Always",
            "containers": [container_spec],
        },
    }


def _make_service(name, port, attack_id, attacker_ip, decoy_type):
    """
    Build a Service spec dict for a decoy pod.

    Each decoy pod gets its own ClusterIP Service so the traffic-router
    can forward attacker traffic to a stable DNS name:
        decoy-fe-<short_id>.decoy-pool.svc.cluster.local
    """
    short_id = attack_id[:8]

    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": DECOY_NAMESPACE,
            "labels": {
                "app": name,
                "role": "decoy",
                "attack-id": short_id,
                "decoy-type": decoy_type,
                "attacker-ip": attacker_ip,
                "app.kubernetes.io/part-of": "deception-system",
                "app.kubernetes.io/component": f"decoy-{decoy_type}",
            },
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "app": name,
            },
            "ports": [
                {
                    "port": port,
                    "targetPort": port,
                    "protocol": "TCP",
                },
            ],
        },
    }
