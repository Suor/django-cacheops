
build_for_test:
	docker build -f tests_Dockerfile -t cacheops_tests .

test: build_for_test
	docker network create cacheops_tests_network > /dev/null 2>&1 || echo 'cacheops_tests_network network already exists'

	docker rm -f cacheops_tests_redis > /dev/null 2>&1 || echo 'No redis container to delete'
	docker run --network=cacheops_tests_network --name cacheops_tests_redis -d -p 6379 redis > /dev/null

	docker run --network=cacheops_tests_network cacheops_tests
	