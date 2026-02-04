.PHONY: help setup verify clean check

help:
	@echo "Decoy Deception System - Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  make check    - Check required dependencies (go, docker/nerdctl)"
	@echo "  make setup    - Install k3s on WSL"
	@echo "  make verify   - Verify k3s installation and memory usage"
	@echo "  make clean    - Uninstall k3s and remove kubeconfig"
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
