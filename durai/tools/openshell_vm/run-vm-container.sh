#!/bin/bash
# Sample: Run in WSL. Start container durai-openshell-vm with /dev/kvm. The driver and the gateway run inside it. The CLI connects on 127.0.0.1:17670.
docker rm -f durai-openshell-vm >/dev/null 2>&1
SP=/home/rms/openshell-spike
sed -e 's#bind_address   = "127.0.0.1:17670"#bind_address   = "0.0.0.0:17670"#' $SP/gateway-vm.toml > $SP/gateway-vm-container.toml
cat > $SP/vm-container-entry.sh <<'EOS'
#!/bin/bash
export OPENSHELL_TELEMETRY_ENABLED=false
mkdir -p /vmstate /run/vm
openshell-driver-vm --bind-socket /run/vm/driver.sock --allow-same-uid-peer --state-dir /vmstate --guest-tls-ca /home/rms/openshell-spike/pki/ca.crt --grpc-endpoint https://host.containers.internal:17670 --mem-mib 384 --gateway-name openshell --default-image nvcr.io/nvidia/base/ubuntu:24.04 > /vmstate/driver.log 2>&1 &
sleep 3
OPENSHELL_COMPUTE_DRIVER=vm OPENSHELL_COMPUTE_DRIVER_SOCKET=/run/vm/driver.sock exec openshell-gateway --config /home/rms/openshell-spike/gateway-vm-container.toml
EOS
chmod +x $SP/vm-container-entry.sh
docker run -d --name durai-openshell-vm --label com.durai.owner=openshell-vm --device /dev/kvm --add-host host.containers.internal:127.0.0.1 \
  -p 127.0.0.1:17670:17670 -v $SP/pki:$SP/pki:ro -v $SP:$SP:ro -v /var/run/docker.sock:/var/run/docker.sock \
  -e OPENSHELL_TELEMETRY_ENABLED=false durai-openshell-vm:1 bash $SP/vm-container-entry.sh
sleep 10
docker logs --tail 8 durai-openshell-vm 2>&1
