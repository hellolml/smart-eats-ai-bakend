.PHONY: help deploy https-enable https-renew logs ps

help:
	@echo "Targets:"
	@echo "  make deploy        # build + up (uses .env.prod)"
	@echo "  make https-enable  # request cert + switch gateway to TLS (single-domain by default)"
	@echo "  make https-renew   # renew cert + reload gateway"
	@echo "  make ps            # show running containers"
	@echo "  make logs          # tail gateway/backend logs"

deploy:
	./deploy/deploy.sh

https-enable:
	./deploy/enable_https.sh

https-renew:
	./deploy/renew_https.sh

ps:
	docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep smart-eats || true

logs:
	@echo "--- gateway ---"; docker logs --tail 120 smart-eats-gateway 2>/dev/null || true
	@echo "--- backend ---"; docker logs --tail 120 smart-eats-backend 2>/dev/null || true
