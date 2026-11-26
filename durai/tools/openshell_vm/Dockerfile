FROM ubuntu:24.04
LABEL com.durai.owner=openshell-vm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates iproute2 && rm -rf /var/lib/apt/lists/*
COPY openshell-driver-vm openshell-gateway /usr/local/bin/
