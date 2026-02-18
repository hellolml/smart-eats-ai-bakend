.PHONY: help deploy https-enable https-renew ps logs

help:
	@echo "Wrapper Makefile. Real targets live in deploy/Makefile"
	@$(MAKE) -f deploy/Makefile help

deploy:
	@$(MAKE) -f deploy/Makefile deploy

https-enable:
	@$(MAKE) -f deploy/Makefile https-enable

https-renew:
	@$(MAKE) -f deploy/Makefile https-renew

ps:
	@$(MAKE) -f deploy/Makefile ps

logs:
	@$(MAKE) -f deploy/Makefile logs
