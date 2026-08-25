COMPOSE = docker compose

.PHONY: help build test run down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

# ── docker e2e ──

build: ## Build the mosh-webrtc e2e image
	$(COMPOSE) build

test: build run ## Build, then run the NAT-traversal e2e test (tears the stack down afterwards)

run: ## Run the e2e test against already built images (CI builds them with a remote cache)
	$(COMPOSE) up -d stun nat_client nat_server server
	$(COMPOSE) run --rm client; rc=$$?; $(COMPOSE) down -v; exit $$rc

down: ## Remove the e2e stack and its networks
	$(COMPOSE) down -v
