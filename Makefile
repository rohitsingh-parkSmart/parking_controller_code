# ParkSmart controller — build / deploy shortcuts.
#
# Thin wrapper around build.sh (which holds the actual buildx logic)
# plus the everyday docker compose operations. Requires `make` and a
# POSIX shell; on Windows run these from Git Bash, or just call
# ./build.sh directly.

SHELL := /usr/bin/env bash

.DEFAULT_GOAL := help
.PHONY: help print build push up down restart logs ps shell clean verify

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

print: ## Show the image tags this build would produce
	@./build.sh print

build: ## Build for this machine's arch and load it locally
	@./build.sh build

push: ## Cross-build arm64+armv7 and push to the registry
	@./build.sh push

# ---------------------------------------------------------------
# Deployment — run these on the Pi (or wherever compose lives).
# ---------------------------------------------------------------

up: ## Start the controller (and Watchtower) in the background
	docker compose up -d

down: ## Stop and remove the containers
	docker compose down

restart: ## Restart just the controller
	docker compose restart controller

logs: ## Follow the controller's logs
	docker compose logs -f controller

ps: ## Show container status
	docker compose ps

shell: ## Open a shell inside the running controller
	docker compose exec controller /bin/bash

verify: ## Check config/tags.json parses and list the loaded tags
	docker compose exec controller python3 -c \
		"import main, json; c=main.ParkSmartController.__new__(main.ParkSmartController); \
		c.tag_map={}; c.companies_by_id={}; main.ParkSmartController.load_tags(c); \
		print(json.dumps(sorted(c.tag_map), indent=2))"

clean: ## Remove the local buildx builder and dangling images
	-docker buildx rm parksmart
	docker image prune -f
