IMAGE_NAME ?= iregul-proxy
IMAGE_TAG ?= local-test

.PHONY: docker-build-test check coverage pre-commit

docker-build-test:
	docker build --pull -t $(IMAGE_NAME):$(IMAGE_TAG) .

check: coverage pre-commit

pre-commit:
	uv run pre-commit run -a

coverage:
	uv run pytest --cov=iregul_proxy --cov-report=term-missing
