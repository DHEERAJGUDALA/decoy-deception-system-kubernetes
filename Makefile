.PHONY: help setup verify clean check build deploy clean-deploy dashboard test test-normal test-sqli test-rate logs clean-images

help:
	@echo "Decoy Deception System - Makefile"
	@echo ""
	@echo "Setup Targets:"
	@echo "  make check         - Check required dependencies (go, docker/nerdctl)"
	@echo "  make setup         - Install k3s on WSL"
	@echo "  make verify        - Verify k3s installation and memory usage"
	@echo "  make clean         - Uninstall k3s and remove kubeconfig"
	@echo ""
	@echo "Deployment Targets:"
	@echo "  make build         - Build all Docker images"
	@echo "  make deploy        - Deploy all services to k3s"
	@echo "  make clean-deploy  - Remove all deployments from k3s"
	@echo "  make clean-images  - Remove all Docker images"
	@echo ""
	@echo "Testing Targets:"
	@echo "  make test          - Run all attack simulations"
	@echo "  make test-normal   - Simulate normal user traffic"
	@echo "  make test-sqli     - Simulate SQL injection attacks"
	@echo "  make test-rate     - Simulate high-rate attack"
	@echo ""
	@echo "Monitoring Targets:"
	@echo "  make dashboard     - Open Controller dashboard in browser"
	@echo "  make logs          - Tail Sentinel logs"
	@echo ""

check:
	@echo "Checking dependencies..."
	@command -v go >/dev/null 2>&1 || { echo "ERROR: go is not installed"; exit 1; }
	@echo "✓ go found: $$(go version)"
	@if command -v docker >/dev/null 2>&1; then \
		echo "✓ docker found: $$(docker --version)"; \
	elif command -v nerdctl >/dev/null 2>&1; then \
		echo "✓ nerdctl found: $$(nerdctl --version)"; \
	else \
		echo "ERROR: neither docker nor nerdctl is installed"; \
		exit 1; \
	fi
	@echo "✓ All dependencies satisfied"

setup:
	@echo "Starting k3s installation..."
	@bash setup/install-k3s-wsl.sh

verify:
	@bash setup/verify-install.sh

clean:
	@echo "Uninstalling k3s..."
	@if command -v k3s-uninstall.sh >/dev/null 2>&1; then \
		sudo k3s-uninstall.sh; \
		echo "✓ k3s uninstalled"; \
	else \
		echo "k3s is not installed or uninstall script not found"; \
	fi
	@if [ -f ~/.kube/config ]; then \
		rm -f ~/.kube/config; \
		echo "✓ ~/.kube/config removed"; \
	fi
	@if [ -d ~/.kube ] && [ -z "$$(ls -A ~/.kube)" ]; then \
		rmdir ~/.kube; \
		echo "✓ ~/.kube directory removed"; \
	fi
	@echo "✓ Cleanup complete"

# Build all Docker images
build:
	@echo "Building Docker images..."
	@bash -c 'cd services/frontend-api && docker build -t frontend-api:latest . && echo "✓ frontend-api built"'
	@bash -c 'cd services/payment-svc && docker build -t payment-svc:latest . && echo "✓ payment-svc built"'
	@bash -c 'cd services/manager && docker build -t manager:latest . && echo "✓ manager built"'
	@bash -c 'cd services/sentinel && docker build -t sentinel:latest . && echo "✓ sentinel built"'
	@bash -c 'cd services/controller && docker build -t controller:latest . && echo "✓ controller built"'
	@bash -c 'cd services/reporter && docker build -t reporter:latest . && echo "✓ reporter built"'
	@echo "✓ All images built successfully"

# Deploy all services to k3s
deploy:
	@echo "Deploying all services..."
	@bash scripts/deploy-all.sh

# Remove all deployments
clean-deploy:
	@echo "Cleaning up deployments..."
	@bash scripts/cleanup.sh

# Remove Docker images
clean-images:
	@echo "Removing Docker images..."
	@docker rmi frontend-api:latest 2>/dev/null || true
	@docker rmi payment-svc:latest 2>/dev/null || true
	@docker rmi manager:latest 2>/dev/null || true
	@docker rmi sentinel:latest 2>/dev/null || true
	@docker rmi controller:latest 2>/dev/null || true
	@docker rmi reporter:latest 2>/dev/null || true
	@echo "✓ Docker images removed"

# Open dashboard in browser
dashboard:
	@NODE_IP=$$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null); \
	if [ -z "$$NODE_IP" ]; then \
		echo "Error: Could not get node IP"; \
		exit 1; \
	fi; \
	echo "Opening dashboard at http://$$NODE_IP:30090"; \
	if command -v wslview >/dev/null 2>&1; then \
		wslview "http://$$NODE_IP:30090"; \
	elif command -v xdg-open >/dev/null 2>&1; then \
		xdg-open "http://$$NODE_IP:30090"; \
	else \
		echo "Please open http://$$NODE_IP:30090 in your browser"; \
	fi

# Run all tests
test: test-normal test-sqli test-rate
	@echo "✓ All tests completed"

# Simulate normal traffic
test-normal:
	@bash scripts/normal-traffic.sh

# Simulate SQL injection attack
test-sqli:
	@bash scripts/sql-injection-attack.sh

# Simulate high-rate attack
test-rate:
	@bash scripts/high-rate-attack.sh

# Tail Sentinel logs
logs:
	@echo "Tailing Sentinel logs (Ctrl+C to exit)..."
	@kubectl logs -l app=sentinel -f

