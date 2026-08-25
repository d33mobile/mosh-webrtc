COMPOSE = docker compose

.PHONY: help build test down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

# ── docker e2e ──

build: ## Build the mosh-webrtc e2e image
	$(COMPOSE) build

test: build ## Run the NAT-traversal e2e test (tears the stack down afterwards)
	$(COMPOSE) up -d stun nat_client nat_server server
	$(COMPOSE) run --rm client; rc=$$?; $(COMPOSE) down -v; exit $$rc

down: ## Remove the e2e stack and its networks
	$(COMPOSE) down -v
