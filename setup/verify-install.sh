#!/bin/bash
set -euo pipefail

echo "========================================="
echo "k3s Installation Verification"
echo "========================================="

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "ERROR: kubectl not found"
    echo "k3s may not be installed correctly"
    exit 1
fi

echo ""
echo "Cluster Status:"
echo "---------------"
kubectl get nodes

echo ""
echo "Node Details:"
echo "-------------"
kubectl get nodes -o wide

echo ""
echo "System Pods:"
echo "------------"
kubectl get pods -n kube-system

echo ""
echo "Memory Usage:"
echo "-------------"

# Get k3s process memory usage
if pgrep k3s > /dev/null; then
    K3S_PID=$(pgrep k3s | head -1)
    K3S_RSS=$(ps -o rss= -p $K3S_PID | awk '{sum+=$1} END {print sum}')
    K3S_RSS_MB=$((K3S_RSS / 1024))

    echo "k3s process RSS: ${K3S_RSS_MB} MB"

    if [ $K3S_RSS_MB -lt 800 ]; then
        echo "✓ Memory usage within target (<800MB)"
    else
        echo "⚠ WARNING: Memory usage exceeds 800MB target"
    fi
else
    echo "ERROR: k3s process not found"
    exit 1
fi

echo ""
echo "Total System Memory:"
free -h

echo ""
echo "========================================="
echo "✓ Verification complete"
echo "========================================="
