# Decoy Deception System

A lightweight Kubernetes-based deception system designed for WSL environments with strict memory constraints.

## System Requirements

- WSL (Windows Subsystem for Linux)
- Total RAM budget: 2.5GB
- k3s memory target: <800MB
- Go (for future phases)
- Docker or nerdctl (for future phases)

## Phase 1: k3s Setup

This phase installs and verifies a lightweight k3s cluster optimized for WSL.

### Quick Start

```bash
# Check dependencies
make check

# Install k3s
make setup

# Verify installation
make verify

# Clean up (removes k3s and kubeconfig)
make clean
```

## Architecture

- **Platform**: k3s on WSL (not Minikube/Kind)
- **Memory-optimized**: Disables traefik, servicelb, metrics-server
- **Logging**: Minimal verbosity (v=2)

## Project Structure

```
decoy-deception-system/
├── README.md
├── Makefile
├── setup/
│   ├── install-k3s-wsl.sh
│   └── verify-install.sh
└── deploy/
```
