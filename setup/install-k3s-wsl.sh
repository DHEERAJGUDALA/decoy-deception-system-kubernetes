#!/bin/bash
set -euo pipefail

echo "========================================="
echo "k3s Installation for WSL (Low Memory)"
echo "========================================="

# Check if running in WSL
if ! grep -qi microsoft /proc/version; then
    echo "ERROR: This script must run in WSL"
    echo "Detected system: $(uname -a)"
    exit 1
fi

echo "✓ WSL environment detected"

# Check if k3s is already installed
if command -v k3s &> /dev/null; then
    echo "WARNING: k3s is already installed"
    k3s --version
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

echo ""
echo "Installing k3s with low-memory configuration..."
echo "Disabled components: traefik, servicelb, metrics-server"
echo ""

# Install k3s with memory-optimized flags
curl -sfL https://get.k3s.io | sh -s - \
    --disable traefik \
    --disable servicelb \
    --disable metrics-server \
    --kube-apiserver-arg=v=2

echo ""
echo "Waiting for k3s to start..."
sleep 5

# Configure kubectl access
sudo mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config
export KUBECONFIG=~/.kube/config

# Add to shell profile if not already present
if ! grep -q "KUBECONFIG=~/.kube/config" ~/.bashrc 2>/dev/null; then
    echo "export KUBECONFIG=~/.kube/config" >> ~/.bashrc
fi

echo ""
echo "========================================="
echo "✓ k3s installation complete!"
echo "========================================="
echo ""
echo "Configuration:"
echo "  - kubeconfig: ~/.kube/config"
echo "  - Run 'source ~/.bashrc' or restart shell"
echo "  - Run 'make verify' to check installation"
echo ""
