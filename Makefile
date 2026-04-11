IMAGE_NAME ?= iregul-proxy
IMAGE_TAG ?= local-test

.PHONY: docker-build-test

docker-build-test:
	docker build --pull -t $(IMAGE_NAME):$(IMAGE_TAG) .
