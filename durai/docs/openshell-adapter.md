# OpenShell sandbox adapter

Status: implemented 2026-11-26 (plan Part D, D1 to D4). Spike report: <plans>\report_openshell_spike_2026-11-26.md.

`durai/adapters/sandbox/openshell.py` runs the `SandboxPort` on an OpenShell gateway. OpenShell is an optional external tool. Durai does not bundle it. Install OpenShell v0.1.10 or later yourself.

## What the adapter does

- It calls the `openshell` command. Set `DURAI_OPENSHELL_CLI` to the command. On Windows with WSL2 use `wsl -d Ubuntu -u <user> -e /home/<user>/openshell-spike/bin/openshell`. Use `-e`, not `--`. The `--` form joins the arguments into one shell string and breaks arguments with brackets or spaces.
- It refuses to start when `OPENSHELL_TELEMETRY_ENABLED` is true. It sets the flag to false for every call. The flag on the gateway process is your duty.
- `create` starts a named sandbox with a policy file, an image and optional providers. It waits for phase Ready. It makes `/tmp/work`.
- `exec` maps `python` to `python3` and passes the deadline as `--timeout`. Exit code 124 means deadline.
- `write_file` and `read_file` use stdin and `cat` inside the sandbox. No path translation is needed.
- `apply_policy` accepts `{"network": "none"}` or `{"network": {"allow": ["host:port"]}}`. The allow-list goes to `openshell policy update --add-endpoint ... --wait` and takes effect without a restart. Other shapes raise `Unsupported`.
- `durai/egress_policy.py` builds the policy file. Landlock is `hard_requirement`. The process user is the string `"1000"`. It refuses localhost, loopback, link-local, `169.254.169.254`, bare names and two-label wildcards.

## Gateway setup that works on one machine

1. `openshell-gateway generate-certs --output-dir <dir> --server-san 127.0.0.1 --server-san localhost`.
2. Gateway TOML with `[openshell] version = 2`, `[openshell.gateway]` (`bind_address`, `compute_driver = "docker"`, `guest_tls_ca`), `[openshell.gateway.tls]`, `[openshell.gateway.gateway_jwt]`, `[openshell.gateway.mtls_auth] enabled = true`, and `[openshell.drivers.docker] supervisor_network_mode = "bridge"`. Do not use `allow_unauthenticated_users`. The Docker driver refuses sandboxes without launch-scoped authentication.
3. Copy `ca.crt`, the client `tls.crt` and `tls.key` to `~/.config/openshell/gateways/openshell/mtls/`. Run `openshell gateway add https://127.0.0.1:17670 --local --name openshell`.
4. Build a sandbox image with `python3` and `curl`. The default image has neither.
5. For model access: `openshell profile import -f <profile> --global` with the DeepInfra profile and your binaries. Then `openshell provider create --name deepinfra --type deepinfra --credential DEEPINFRA_API_KEY` with the key in the environment. The sandbox sees only a placeholder.

## Tests

- `tests/test_openshell_adapter.py` and `tests/test_egress_policy.py` run with no OpenShell.
- `tests/test_contract_sandbox.py` runs the shared contract on `OpenShellSandbox` when `DURAI_OPENSHELL_CLI` is set and `openshell status` succeeds. Set `DURAI_OPENSHELL_IMAGE` to your image tag. The default is `durai-spike-sandbox:2`.
- The OpenShell tests also check the live egress change, kernel-denied writes, and uid 1000.

## Limits

- Python of the worker must be in the image. The Deep Agents stack is not yet run inside a sandbox (plan Part D6).
- The deadline kills the local call. The process in the sandbox ends through `--timeout`.
- OpenShell is v0.1.x. Interfaces may change in a patch release. Pin the version.

## Worker inside a sandbox and model-driven escape tests

- `tools/run_worker_in_sandbox.py` builds `durai-worker-sandbox:1` (deepagents, langchain-openai, psycopg and the Durai package) and runs `coding_worker.repair_workspace` inside an OpenShell sandbox. The key stays a placeholder. The receipt is `receipts/worker-in-openshell.json`.
- `tools/run_adversary.py openshell` asks DeepSeek for six escape programs, runs them in one sandbox and checks the scripted invariants after each run. The receipt is `receipts/sandbox-adversary-openshell.json`.
- DeepInfra can answer 429 "Model busy". The tools retry with a wait.

## MicroVM driver (not working on Ubuntu 20.04)

`openshell-driver-vm` runs on a socket and the gateway accepts `compute_driver = "vm"` with `OPENSHELL_COMPUTE_DRIVER_SOCKET`. The VM launch fails on a host with glibc older than 2.32 because `libkrun.so` needs GLIBC_2.32. Use a newer distro or a container with `/dev/kvm`. The driver must stay in a live session (`run-driver-vm.sh`).

## MicroVM runtime in a container (D5, works)

The VM driver needs glibc 2.32 or newer. Ubuntu 20.04 has 2.31, so the driver and the gateway run in a container built from `ubuntu:24.04`.

1. Windows: `nestedVirtualization=true` in `%USERPROFILE%\.wslconfig`, then `wsl --shutdown`.
2. WSL, after every shutdown: `modprobe kvm_intel || modprobe kvm_amd` and `chmod 666 /dev/kvm` (as root).
3. Docker Desktop passes `--device /dev/kvm` to a container. Build `tools/openshell_vm/Dockerfile` (it needs `openshell-driver-vm` and `openshell-gateway` next to it) as `durai-openshell-vm:1`.
4. Run `tools/openshell_vm/run-vm-container.sh` in WSL. The CLI connects on 127.0.0.1:17670 with the same mTLS files. The script mounts the Docker socket so the driver can export the image.
5. `--mem-mib 384` caps the VM memory. The default 2048 MiB lets the 512 MiB memory probe pass. The per-sandbox `--memory` flag does not shrink the VM.
6. Result: contract tests 49 passed. Escape suite 7 of 7 with the 384 MiB cap. Receipt `receipts/sandbox-escape-openshell-vm.json`. The fork bomb reached 300 children, the same as the Docker driver of OpenShell.
7. The container mounts the Docker socket. That gives it control of the Docker daemon. Use it on a development machine only.
