# Docker

A [Dockerfile](https://github.com/omiinaya/browser-relay/blob/master/Dockerfile) is provided for running the relay server in a container. The image uses a two-stage build for a slim runtime image.

## Quick Start

```bash
# Build the image
make docker-build
# or: docker build -t browser-relay .

# Run the container
docker run -d -p 9223:9223 --name browser-relay browser-relay
```

The relay is now available at `http://localhost:9223`.

## Network Host Mode

To use with a Chrome instance running on the host machine, pass `--network=host`:

```bash
docker run -d --network=host --name browser-relay browser-relay
```

This lets the relay connect to Chrome's debugger ports on the host's network stack.

## Configuration

The Docker image supports the same flags as `server.py`:

```bash
docker run -d -p 9223:9223 browser-relay --host 0.0.0.0 --port 9223 --log-level DEBUG
```

## Health Check

The container includes a Docker health check that pings `/status` every 30 seconds. If the relay stops responding, the container will be marked `unhealthy` after 3 failures.

## Stopping

```bash
docker stop browser-relay
docker rm browser-relay
```

Or use the Makefile:

```bash
make docker-stop
```
