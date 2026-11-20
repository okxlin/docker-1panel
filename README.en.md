# 1Panel Docker Images

Languages: [English](README.en.md) | [Simplified Chinese](README.md)

[![Docker Image Version](https://img.shields.io/docker/v/moelin/1panel/latest?color=%2348BB78&logo=docker&label=version)](https://hub.docker.com/r/moelin/1panel)
[![Docker Pulls](https://img.shields.io/docker/pulls/moelin/1panel?color=%2348BB78&logo=docker&label=pulls)](https://hub.docker.com/r/moelin/1panel)
[![Docker Stars](https://img.shields.io/docker/stars/moelin/1panel?color=%2348BB78&logo=docker&label=stars)](https://hub.docker.com/r/moelin/1panel)
[![GitHub Stars](https://img.shields.io/github/stars/okxlin/docker-1panel)](https://github.com/okxlin/docker-1panel)

This project provides container images for 1Panel. It supports V1/V2, CN builds, and Global/Intl builds. Images use an Ubuntu LTS base and run 1Panel processes through Supervisor.

> [!CAUTION]
> 1Panel V1 cannot upgrade to V2 in place. If you need to migrate existing V1 data to V2, read the official migration guide first: <https://1panel.cn/docs/v2/installation/v1_migrate/>

## Versions And Images

| Series | Source | Dockerfile | Latest source version | Use case |
| --- | --- | --- | --- | --- |
| V1 CN | `resource.fit2cloud.com` | `V1/Dockerfile` | `v1.10.34-lts` | Maintain existing V1 deployments |
| V1 Global | `resource.1panel.pro` | `V1/Dockerfile-Global` | `v1.10.34-lts` | Maintain existing Global V1 deployments |
| V2 CN | `resource.fit2cloud.com/1panel/package/v2` | `V2/Dockerfile` | `v2.5.1` | Recommended for new CN deployments |
| V2 Global | `resource.1panel.pro/v2` | `V2/Dockerfile-Global` | `v2.5.1` | Recommended for new Global deployments |

Base image: `ubuntu:26.04`

Common tags:

```bash
# V1 CN
moelin/1panel:v1.10.34-lts
moelin/1panel:v1

# V1 Global
moelin/1panel:global-v1.10.34-lts
moelin/1panel:global-v1

# V2 CN
moelin/1panel:v2.5.1
moelin/1panel:v2
moelin/1panel:latest

# V2 Global
moelin/1panel:global-v2.5.1
moelin/1panel:global-v2
```

Pin a version tag in production. Floating tags such as `v1`, `v2`, `global-v1`, and `global-v2` work better for testing.

## Deployment

### Docker Run

V2 CN:

```bash
docker run -d \
  --name 1panel-v2 \
  --restart always \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt:/opt \
  -e TZ=Asia/Shanghai \
  -e PORT=10086 \
  -e USERNAME=admin \
  -e PASSWORD=your_secure_password \
  -e ENTRANCE=myentrance \
  moelin/1panel:v2
```

V2 Global:

```bash
docker run -d \
  --name 1panel-v2-global \
  --restart always \
  --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt:/opt \
  -e TZ=Asia/Shanghai \
  -e PORT=10086 \
  -e USERNAME=admin \
  -e PASSWORD=your_secure_password \
  -e ENTRANCE=myentrance \
  moelin/1panel:global-v2
```

V1 uses the same run pattern. Replace the image with `moelin/1panel:v1` or `moelin/1panel:global-v1`. For full compatibility with older V1 deployments, you may also mount `/var/lib/docker/volumes` and `/root`:

```bash
-v /var/lib/docker/volumes:/var/lib/docker/volumes \
-v /root:/root
```

### Docker Compose

```yaml
services:
  1panel:
    image: moelin/1panel:v2
    container_name: 1panel-v2
    restart: always
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /opt:/opt
    environment:
      - TZ=Asia/Shanghai
      - PORT=10086
      - USERNAME=admin
      - PASSWORD=your_secure_password
      - ENTRANCE=myentrance
      - BASE_DIR=/opt
    labels:
      createdBy: Apps
```

Start it:

```bash
docker compose up -d
```

For V2 Global, change the image:

```yaml
image: moelin/1panel:global-v2
```

### V2 Virtual Machines (KVM)

The V2 `amd64` and `arm64` images include libvirt clients and `qemu-img`. They connect to libvirt on the same host to manage upstream-supported VMs, ISOs, templates, VM snapshots, networks and storage pools. Libvirt and the VMs run on the host, so recreating the panel container does not stop the VMs. Use the existing configuration above for ordinary deployments.

1Panel opened VM management to the Community edition in [v2.5.0](https://github.com/1Panel-dev/1Panel/releases/tag/v2.5.0). V2.5.1 allows 1 VM in Community, 5 in Professional, and unlimited VMs in Enterprise; other licensing rules remain upstream-controlled. V1 and other CPU architectures do not support this feature.

The Linux host needs KVM, an accessible `/dev/kvm`, and installed, running QEMU/libvirt.

On a Debian 12 `amd64` host using the traditional `libvirtd` mode, run as root:

```bash
apt-get update
apt-get install -y qemu-system-x86 libvirt-daemon-system libvirt-clients
systemctl start libvirtd.socket virtlogd.socket virtlockd.socket
```

Then check on the host:

```bash
test -c /dev/kvm
virsh --connect qemu:///system list --all
mkdir -p /var/lib/libvirt/images /var/lib/libvirt/boot
```

Use your distribution's libvirt services and QEMU packages. Modular daemons need the corresponding QEMU, network and storage services/sockets; the example mounts the entire `/run/libvirt` directory so socket replacement works. ARM64 hosts also need `qemu-system-aarch64` and `qemu-efi-aarch64`, with UEFI firmware accessible at the same paths on the host and in the panel container. The image includes `qemu-efi-aarch64`; mount the firmware directory at its original path when using another distribution's firmware.

Add the KVM and libvirt mounts from [V2/compose.host-libvirt.yml](V2/compose.host-libvirt.yml) to your existing configuration, or start the example from the repository root:

```bash
export PANEL_PASSWORD='your_secure_password'
# For Global, set PANEL_IMAGE=moelin/1panel:global-v2 before starting
docker compose -f V2/compose.host-libvirt.yml up -d
```

When migrating an existing container, keep its data mounts, `BASE_DIR`, port, entrance and account configuration, and check the `.docker_initialized` marker described below. The example's `PANEL_*` variables are only Compose substitutions.

| Mount/configuration | Purpose |
| --- | --- |
| `/dev/kvm` | Panel environment checks; host libvirt starts the VMs using KVM |
| `/run/libvirt` | Connection to host libvirt Unix sockets |
| `/var/lib/libvirt/images` | Default VM disks, templates and VM snapshot files |
| `/var/lib/libvirt/boot` | Libvirt's default ISO directory |
| `network_mode: host` | Host interface/bridge discovery and access to local libvirt VNC consoles |

Mount custom disk, ISO and storage-pool directories read-write at **the same absolute paths on the host and container**. Otherwise files created or converted by the panel cannot be used by host QEMU. Preserve the host's QEMU file permissions and SELinux/AppArmor policy. Back up VM disks and libvirt configuration separately; panel system snapshots do not cover these external directories.

> [!CAUTION]
> The system libvirt socket grants control to create, modify and delete host VMs, with host administrator privileges. A read-only directory mount of `/run/libvirt` does not restrict socket management calls. Give this mount only to a trusted panel and administrators. The example requires no `privileged` mode, host PID namespace, host root filesystem or `/sys/fs/cgroup` mount.

Run `virsh --connect qemu:///system list --all` inside the container to check connectivity. Panel environment checks report missing devices, directories and sockets. The page's environment-start operation cannot start host services; manage libvirt on the host. NAT, bridges, macvtap and device passthrough depend on host networking/hardware and the upstream interface; adding a device mapping alone does not enable them.

The project uses isolated real libvirt/TCG tests for client connectivity, resources and lifecycle operations. On a Debian 12 `amd64` host, both CN and Global images also passed native KVM startup, panel discovery, suspend and resume checks, with hardware acceleration confirmed by `query-kvm`. These native KVM tests used transient VMs without disks or network interfaces; verify guest installation, networking and browser console access on the deployment host. See libvirt's [Connection URIs](https://libvirt.org/uri.html#qemu-qemu-and-kvm-uris) and [Libvirt Daemons](https://libvirt.org/daemons.html#operating-modes) for connection and permission details.

## Environment Variables

| Variable | Default | Description |
| --- | --- | --- |
| `PORT` | `10086` | Panel access port |
| `USERNAME` | `1panel` | Administrator username |
| `PASSWORD` | `1panel_password` | Administrator password. Empty values and the default value generate a random password on first startup |
| `ENTRANCE` | `entrance` | Security entrance path, without leading or trailing `/` |
| `BASE_DIR` | `/opt` | 1Panel base path. Panel data inside the container is stored at `${BASE_DIR}/1panel` and must be covered by the data mount. When deploying applications through the host Docker socket, use the same absolute paths on the host and container |
| `TZ` | `Asia/Shanghai` | Container timezone |
| `RESET` | `false` | Set to `true` to reset username, password, port, and entrance on startup |

For a new deployment with a different data path, mount `/srv/panel:/srv/panel` and set `BASE_DIR=/srv/panel`. Panel data is stored at `/srv/panel/1panel`. Keep the original data mounts and `BASE_DIR` when upgrading an existing deployment.

The container logs print the random password:

```bash
docker logs 1panel-v2
```

> [!IMPORTANT]
> V1 environment-variable initialization applies only to `v1.10.34-lts` and later. Keep older V1 images on their original deployment flow.

## Existing Data And Password Resets

The entrypoint uses `.docker_initialized` in the data directory to decide whether Docker initialization has already run. Its default container path is `/opt/1panel/.docker_initialized`; with `BASE_DIR=/data`, it is `/data/1panel/.docker_initialized`. The host location depends on the data mount's `source` and `target` mapping.

If you have existing data and the database exists without this marker file, the entrypoint treats the container as a first startup. It then applies `USERNAME`, `PASSWORD`, `PORT`, and `ENTRANCE`, which can change the old username, password, port, and entrance. To keep existing credentials, create the marker before starting the new container:

```bash
# Run on the host; these examples require identical host source and container target paths
# Default BASE_DIR=/opt
touch /opt/1panel/.docker_initialized

# When BASE_DIR=/data
touch /data/1panel/.docker_initialized
```

Do not set `RESET=true` for existing data you want to preserve. `RESET=true` forces username, password, port, and entrance configuration even when `.docker_initialized` exists.

Database paths:

```bash
# V1
/opt/1panel/db/1Panel.db

# V2
/opt/1panel/db/core.db
/opt/1panel/db/agent.db
```

## Snapshots

At startup, the image configures panel service calls to use Supervisor for V1 snapshot creation and V2 snapshot recovery. V2 service files have distinct names so they cannot overwrite panel binaries in the snapshot archive.

Create a fresh snapshot after updating the image. Restore with the same panel major version, CPU architecture, and `BASE_DIR`.

Scheduled builds pull the Ubuntu 26.04 base image, upgrade installed packages, and disable build caching to obtain the security updates available for each architecture.

### V2 Container And Compose Backups

Docker mount sources belong to the host filesystem. Access to the Docker socket lets the panel inspect application containers, but does not let it read files that are absent from its own mounts. V2.5.1 may skip inaccessible data while marking the backup task successful. This image provides manual checks; **it does not change the upstream UI's success decision**.

Before backing up, check selected containers or a Compose project inside the panel container:

```bash
docker exec 1panel-v2 1panel-backup-check preflight \
  --panel-container 1panel-v2 --container my-app

# Alternatively, select a Compose project; --container can also be repeated
docker exec 1panel-v2 1panel-backup-check preflight \
  --panel-container 1panel-v2 --compose-project my-project
```

The tool uses the panel's configured same-host Unix Docker socket. It checks that actual bind and local named-volume `Source` paths have readable **same-path mounts** in the panel. An unrelated existing container directory does not prove access to host data; nested mounts are checked too. Add selected data mounts from [V2/compose.host-data.yml](V2/compose.host-data.yml) to your existing configuration, keeping its data directory, credentials and ports. For example:

```bash
export PANEL_BACKUP_SOURCE=/srv/my-app/data
export PANEL_PASSWORD='your_secure_password'
docker compose -f V2/compose.host-data.yml config
# Recreate the panel only after checking the configuration
docker compose -f V2/compose.host-data.yml up -d
```

The example mounts one explicitly selected, existing absolute path read-only. Repeat the mount for other sources. On the host, use `docker volume inspect --format '{{.Mountpoint}}' my-volume` to find a named volume's source path. Avoid mounting the entire host root for a single backup. Restoring data needs an explicitly writable source mount and a separate restore test first.

After the panel finishes its backup, check the container/Compose `.tar.gz`. Replace the path below with the actual archive path readable inside the panel container:

```bash
docker exec 1panel-v2 1panel-backup-check verify \
  /opt/1panel/backup/your-backup.tar.gz
```

Exit code `0` means the check passed. A nonzero exit with `status: failed` means you should not rely on that backup. Without extracting files, `verify` checks correspondence between the manifest, inspect data, persistent mount payloads, named-volume metadata and declared Compose files; it explicitly excludes tmpfs. Strict checking rejects links, special files and archives exceeding its budgets, which default to 100,000 tar headers and 64 GiB of cumulative decompressed bytes. Adjust them with `--max-members` and `--max-bytes`. These checks cannot prove original-file checksums, consistency of a live database, the presence of an undeclared `.env`, or successful restoration. Important applications still need paused writes or native backups and an independent restore drill.

## V2 SSH And Supervisor

V2 images include the OpenSSH client and support panel-generated Ed25519, RSA and ECDSA keys. By default, `/root/.ssh` links to `ssh/` in the persistent panel data directory, `${BASE_DIR}/1panel/ssh` (`/opt/1panel/ssh` by default), preserving the database's `/root/.ssh/...` paths. Reuse the original panel data mount when recreating the container to keep keys and `authorized_keys`. Include this directory in protected backups because it contains private keys.

On upgrade, an existing ordinary SSH directory is preserved and migrated, with the original left at `/root/.ssh.before-1panel`. Conflicts, unknown links and nested mounts stop startup to avoid overwriting keys. Existing external `/root` or `/root/.ssh` mounts are kept in place. Keys stored only in an old container cannot be recovered automatically after that container is deleted; save them before upgrading. [OpenSSH removed DSA from 10.0 onward](https://www.openssh.org/txt/release-10.0), so replace old DSA keys with a supported algorithm. V2's local terminal connects to the target host over SSH and needs that host's SSH service and valid credentials.

Legacy SSH directory migration requires filesystem support for [Linux atomic rename without replacement](https://man7.org/linux/man-pages/man2/rename.2.html) (`renameat2(RENAME_NOREPLACE)`); if unsupported, startup stops with an explicit error and preserves the original directory and keys, and support on macOS/VirtioFS remains untested.

Normal V2 deployments keep the original data mounts, `BASE_DIR`, port, entrance and account configuration. Default SSH keys remain in the panel data directory's `ssh/` subdirectory. This permission compatibility fix requires no additional SSH volume or Compose change. The image preserves the selected panel data directory's host UID and existing permissions.

Default persistent SSH storage and the setup lock file may belong to the panel runtime user (`root` in this image) or the selected data directory's owner. Other owners, unknown links, hard links and storage conflicts still stop startup; SSH directories still reject group or other write permissions. The panel database, configuration and SSH state share the selected data directory's deployment trust boundary. Give write access only to trusted users; private permissions on the SSH subdirectory do not replace that requirement.

[Docker Desktop on macOS retains the original permissions of host bind-mounted directories](https://docs.docker.com/desktop/setup/install/mac-permission-requirements/#containers-running-as-root-within-the-linux-vm). Its [4.80.0 VirtioFS release notes](https://docs.docker.com/desktop/release-notes/#4800) state: “Calls to `chown` will succeed, but `stat` will not be affected.” A successful `chown` therefore does not prove that the visible UID became container `root`; recursively changing ownership or permissions on the host data directory is unnecessary.

An independent `/root/.ssh` volume remains an optional storage choice managed by the user. Use a separate volume for each panel, reuse it on recreation (keeping the project name for Compose deployments), and include it in protected backups. [Named-volume data exists independently of the container's lifecycle](https://docs.docker.com/engine/storage/volumes/#a-volumes-lifecycle). Do not mount your personal macOS `~/.ssh`. A new empty volume does not migrate existing `/opt/1panel/ssh` keys, including the local key generated automatically at first startup. Preserve and explicitly migrate existing keys before switching storage; do not switch straight to an empty volume or delete the old container first.

Panel Supervisor uses its own `/etc/1panel/supervisord.conf`, `/run/1panel/supervisor.sock` and PID file. The toolbox's business Supervisor can initialize, manage processes and restart itself while panel processes keep running. `1pctl` and the panel service bridge explicitly select panel Supervisor.

Business Supervisor configuration remains in `/etc/supervisor`, with worker configuration/logs in the panel data directory. Keeping `/opt` alone does not guarantee restoration of daemon include configuration or running state after recreation. To keep business configuration, persist `/etc/supervisor` separately, then start business Supervisor in the toolbox and check its processes after recreation. Reinitialize it if the configuration was not kept. It manages processes inside the container and does not replace host systemd service management.

## Build

Local single-architecture builds:

```bash
# V2 CN
docker build -t 1panel:v2 ./V2

# V2 Global
docker build -f ./V2/Dockerfile-Global -t 1panel:global-v2 ./V2
```

Build a specific version:

```bash
docker build \
  --build-arg PANELVER=v2.5.1 \
  -t 1panel:v2.5.1 \
  ./V2

docker build \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.5.1 \
  -t 1panel:global-v2.5.1 \
  ./V2
```

Multi-architecture build and push:

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  --build-arg PANELVER=v2.5.1 \
  -t <your-dockerhub-username>/1panel:v2.5.1 \
  -t <your-dockerhub-username>/1panel:v2 \
  --push \
  ./V2

docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7,linux/ppc64le,linux/s390x \
  -f ./V2/Dockerfile-Global \
  --build-arg PANELVER=v2.5.1 \
  -t <your-dockerhub-username>/1panel:global-v2.5.1 \
  -t <your-dockerhub-username>/1panel:global-v2 \
  --push \
  ./V2
```

V1 builds:

```bash
docker build --build-arg PANELVER=v1.10.34-lts -t 1panel:v1 ./V1
docker build -f ./V1/Dockerfile-Global --build-arg PANELVER=v1.10.34-lts -t 1panel:global-v1 ./V1
```

## FAQ

### Which one should I use, V1 or V2?

Use V2 for new deployments. If an existing V1 deployment works well, you can keep using the V1 image. Back up your data and follow the official migration flow before moving from V1 to V2.

### How do I migrate a Docker-based V1 deployment to V2?

You can use the migration script to switch V1 from Docker mode to host mode, then upgrade to V2 with the official guide:

```bash
wget -O 1panel_docker_to_sys.sh https://raw.githubusercontent.com/okxlin/ToolScript/refs/heads/main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
chmod +x 1panel_docker_to_sys.sh
bash 1panel_docker_to_sys.sh
```

For networks in mainland China, replace the download URL with the jsDelivr mirror:

```bash
https://cdn.jsdelivr.net/gh/okxlin/ToolScript@main/1Panel/1panel-execution-mode/1panel_docker_to_sys.sh
```

Official migration guide: <https://1panel.cn/docs/v2/installation/v1_migrate/>

### How do I run 1pctl inside the container?

```bash
docker exec -it 1panel-v2 bash
1pctl version
1pctl user-info
```

### Why should I avoid clicking upgrade inside the panel?

The image contains a specific 1Panel binary version and its initialization files. Pull a new image and redeploy when you need to upgrade, so the container filesystem stays aligned with the image version.

## Links

- [1Panel CN website](https://1panel.cn)
- [1Panel Global website](https://1panel.pro)
- [1Panel GitHub](https://github.com/1Panel-dev/1Panel)
- [Docker Hub](https://hub.docker.com/r/moelin/1panel)
- [This project on GitHub](https://github.com/okxlin/docker-1panel)
- [App store compatibility repository](https://github.com/okxlin/appstore)
