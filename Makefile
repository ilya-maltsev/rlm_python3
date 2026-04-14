VERSION := 0.1.0
IMAGE_NAME := privacyidea-freeradius-python:$(VERSION)

BUILDER := docker build
CONTAINER_ENGINE := docker
COMPOSE := docker compose

REGISTRY := localhost:5000

# --- Build ---

build:
	$(BUILDER) --no-cache -t $(IMAGE_NAME) .

push:
	$(CONTAINER_ENGINE) tag $(IMAGE_NAME) $(REGISTRY)/$(IMAGE_NAME)
	$(CONTAINER_ENGINE) push $(REGISTRY)/$(IMAGE_NAME)

# --- Compose ---

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f freeradius

ps:
	$(COMPOSE) ps

# --- Cleanup ---

clean: down
	$(CONTAINER_ENGINE) rmi $(IMAGE_NAME) 2>/dev/null || true

.PHONY: build push up down logs ps clean
