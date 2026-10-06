from __future__ import annotations

import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import docker

from .config import Settings


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_docker_time(value: str | None) -> int | None:
    if not value or value.startswith("0001-"):
        return None
    normalized = value.replace("Z", "+00:00")
    if "." in normalized:
        left, right = normalized.split(".", 1)
        if "+" in right:
            fraction, tz = right.split("+", 1)
            normalized = f"{left}.{fraction[:6]}+{tz}"
        elif "-" in right:
            fraction, tz = right.split("-", 1)
            normalized = f"{left}.{fraction[:6]}-{tz}"
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _status_from_container(status: str | None, health: str | None) -> str:
    normalized = (status or "").lower()
    health_status = (health or "").lower()
    if normalized == "running":
        if health_status == "unhealthy":
            return "degraded"
        return "healthy"
    if normalized in {"created", "exited", "dead", "removing"}:
        return "down"
    if normalized in {"restarting", "paused"}:
        return "degraded"
    return "unknown"


def _host_path(path: Path, host_root: Path) -> str:
    try:
        relative = path.relative_to(host_root)
        return "/" + str(relative)
    except ValueError:
        return str(path)


def _split_port(value: str) -> tuple[str, str]:
    if "/" not in value:
        return value, "tcp"
    port, protocol = value.rsplit("/", 1)
    return port, protocol.lower()


def _is_loopback(host_ip: str) -> bool:
    if host_ip in {"localhost", "::1"}:
        return True
    try:
        return socket.getaddrinfo(host_ip, None)[0][4][0].startswith("127.")
    except OSError:
        return host_ip.startswith("127.")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_cloudflared_config(text: str) -> dict[str, Any]:
    tunnel = ""
    ingress: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("tunnel:"):
            tunnel = _strip_quotes(line.split(":", 1)[1])
            continue
        if line.startswith("- hostname:"):
            if current:
                ingress.append(current)
            current = {"hostname": _strip_quotes(line.split(":", 1)[1])}
            continue
        if line.startswith("- service:"):
            if current:
                ingress.append(current)
            current = {"service": _strip_quotes(line.split(":", 1)[1])}
            continue
        if line.startswith("hostname:") and current is not None:
            current["hostname"] = _strip_quotes(line.split(":", 1)[1])
            continue
        if line.startswith("service:") and current is not None:
            current["service"] = _strip_quotes(line.split(":", 1)[1])

    if current:
        ingress.append(current)

    return {
        "tunnel": tunnel,
        "ingress": [item for item in ingress if item.get("hostname")],
    }


def iter_nginx_server_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    for match in re.finditer(r"\bserver\s*\{", text):
        start = match.start()
        depth = 0
        for index in range(match.end() - 1, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : index + 1])
                    break
    return blocks


def parse_npm_config(text: str, source: str) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for block in iter_nginx_server_blocks(text):
        server_name_match = re.search(r"^\s*server_name\s+([^;]+);", block, re.MULTILINE)
        if not server_name_match:
            continue
        hostnames = [
            item
            for item in server_name_match.group(1).split()
            if item and item not in {"_", "localhost"}
        ]
        if not hostnames:
            continue

        scheme = "http"
        scheme_match = re.search(r"set\s+\$forward_scheme\s+([^;]+);", block)
        if scheme_match:
            scheme = _strip_quotes(scheme_match.group(1))

        target = ""
        target_match = re.search(r"set\s+\$server\s+([^;]+);", block)
        if target_match:
            target = _strip_quotes(target_match.group(1))

        port = 80 if scheme == "http" else 443
        port_match = re.search(r"set\s+\$port\s+([^;]+);", block)
        if port_match:
            port = _safe_int(_strip_quotes(port_match.group(1)), port)

        proxy_match = re.search(r"proxy_pass\s+(https?)://([^/:;$]+)(?::(\d+))?", block)
        if not target and proxy_match:
            scheme = proxy_match.group(1)
            target = proxy_match.group(2)
            port = _safe_int(proxy_match.group(3), port)

        if not target:
            continue

        for hostname in hostnames:
            routes.append(
                {
                    "hostname": hostname,
                    "scheme": scheme,
                    "forward_host": target,
                    "forward_port": port,
                    "source": source,
                }
            )
    return routes


class TopologyCollector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._docker_client: docker.DockerClient | None = None
        self._cache: tuple[float, dict[str, Any]] | None = None

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self.settings.topology_cache_seconds:
            return self._cache[1]

        annotations = self._annotations()
        docker_data = self._docker_runtime()
        tunnels = self._cloudflared_tunnels(annotations)
        npm_routes = self._npm_routes()
        snapshot = self._build_graph(
            annotations=annotations,
            docker_data=docker_data,
            tunnels=tunnels,
            npm_routes=npm_routes,
        )
        self._cache = (now, snapshot)
        return snapshot

    def _annotations(self) -> dict[str, Any]:
        path = self.settings.topology_annotations_path
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(_read_text(path, "{}"))
                if isinstance(loaded, dict):
                    payload = loaded
            except json.JSONDecodeError:
                payload = {
                    "warnings": [
                        {
                            "kind": "annotation_parse",
                            "severity": "warning",
                            "message": f"Cannot parse {path}",
                        }
                    ]
                }
        payload.setdefault("tunnels", {})
        payload.setdefault("direct_host_services", {})
        payload.setdefault("warnings", [])
        return payload

    def _get_docker_client(self) -> docker.DockerClient | None:
        if not Path("/var/run/docker.sock").exists():
            return None
        if self._docker_client is None:
            try:
                self._docker_client = docker.DockerClient(
                    base_url="unix://var/run/docker.sock",
                    timeout=self.settings.docker_timeout_seconds,
                )
            except docker.errors.DockerException:
                self._docker_client = None
        return self._docker_client

    def _docker_runtime(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": False,
            "error": None,
            "containers": [],
            "containers_by_name": {},
            "networks": [],
            "networks_by_name": {},
        }
        client = self._get_docker_client()
        if client is None:
            result["error"] = "Docker socket not available"
            return result
        try:
            client.ping()
            containers = client.containers.list(all=True)
            networks = client.networks.list()
        except docker.errors.DockerException as exc:
            self._docker_client = None
            result["error"] = str(exc)[:240]
            return result

        container_rows = [self._container_row(container) for container in containers]
        container_rows.sort(key=lambda item: item["name"])
        network_rows = [self._network_row(network) for network in networks]
        network_rows.sort(key=lambda item: item["name"])
        result.update(
            {
                "available": True,
                "containers": container_rows,
                "containers_by_name": {item["name"]: item for item in container_rows},
                "networks": network_rows,
                "networks_by_name": {item["name"]: item for item in network_rows},
            }
        )
        return result

    def _container_row(self, container: Any) -> dict[str, Any]:
        try:
            attrs = container.attrs
        except docker.errors.DockerException:
            attrs = {}
        state = attrs.get("State", {})
        config = attrs.get("Config", {})
        labels = config.get("Labels") or {}
        status = state.get("Status", getattr(container, "status", "unknown"))
        health = state.get("Health", {}).get("Status")
        started_at = _parse_docker_time(state.get("StartedAt"))
        uptime_seconds = (
            max(int(time.time()) - started_at, 0)
            if started_at and status == "running"
            else 0
        )
        exposed_ports = sorted((config.get("ExposedPorts") or {}).keys())
        published_ports = self._published_ports(attrs)
        networks = sorted(
            (attrs.get("NetworkSettings", {}).get("Networks") or {}).keys()
        )
        compose = {
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
        }
        return {
            "id": (attrs.get("Id") or getattr(container, "id", ""))[:12],
            "full_id": attrs.get("Id") or getattr(container, "id", ""),
            "name": getattr(container, "name", None)
            or attrs.get("Name", "").lstrip("/")
            or (attrs.get("Id") or "")[:12],
            "image": config.get("Image") or attrs.get("Image", ""),
            "status": status,
            "health": health,
            "node_status": _status_from_container(status, health),
            "uptime_seconds": uptime_seconds,
            "restart_count": attrs.get("RestartCount", 0),
            "networks": networks,
            "exposed_ports": exposed_ports,
            "published_ports": published_ports,
            "compose": {k: v for k, v in compose.items() if v},
        }

    def _published_ports(self, attrs: dict[str, Any]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
        for container_port, bindings in ports.items():
            private_port, protocol = _split_port(container_port)
            if not bindings:
                continue
            for binding in bindings:
                rows.append(
                    {
                        "host_ip": binding.get("HostIp") or "0.0.0.0",
                        "host_port": binding.get("HostPort") or "",
                        "container_port": private_port,
                        "protocol": protocol,
                    }
                )
        rows.sort(key=lambda item: (item["host_port"], item["protocol"], item["host_ip"]))
        return rows

    def _network_row(self, network: Any) -> dict[str, Any]:
        attrs = network.attrs
        containers = attrs.get("Containers") or {}
        members = sorted(
            (item.get("Name") or "").lstrip("/")
            for item in containers.values()
            if item.get("Name")
        )
        return {
            "id": attrs.get("Id", "")[:12],
            "name": attrs.get("Name") or getattr(network, "name", ""),
            "driver": attrs.get("Driver"),
            "scope": attrs.get("Scope"),
            "internal": bool(attrs.get("Internal")),
            "members": members,
        }

    def _running_cloudflared_configs(self) -> set[str]:
        configs: set[str] = set()
        proc_root = self.settings.host_proc
        if not proc_root.exists():
            return configs
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if b"cloudflared" not in raw:
                continue
            parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]
            for index, part in enumerate(parts):
                if part == "--config" and index + 1 < len(parts):
                    configs.add(parts[index + 1])
                elif part.startswith("--config="):
                    configs.add(part.split("=", 1)[1])
        return configs

    def _systemd_cloudflared_services(self) -> dict[str, dict[str, str]]:
        services: dict[str, dict[str, str]] = {}
        service_dir = self.settings.host_root / "etc/systemd/system"
        if not service_dir.exists():
            return services
        for path in service_dir.glob("*cloudflared*.service"):
            text = _read_text(path)
            match = re.search(r"ExecStart=.*?--config(?:=|\s+)(\S+)", text)
            if not match:
                continue
            service_name = path.stem
            tunnel_name = ""
            if service_name.startswith("cloudflared-"):
                tunnel_name = service_name.removeprefix("cloudflared-")
            services[match.group(1)] = {
                "service": path.name,
                "tunnel_name": tunnel_name,
            }
        return services

    def _cloudflared_tunnels(self, annotations: dict[str, Any]) -> list[dict[str, Any]]:
        tunnels: list[dict[str, Any]] = []
        config_dir = self.settings.cloudflared_config_dir
        if not config_dir.exists():
            return tunnels
        running_configs = self._running_cloudflared_configs()
        systemd_services = self._systemd_cloudflared_services()
        tunnel_annotations = annotations.get("tunnels") or {}

        for path in sorted(config_dir.glob("*.yml")):
            parsed = parse_cloudflared_config(_read_text(path))
            tunnel_id = parsed.get("tunnel") or path.stem
            host_config = _host_path(path, self.settings.host_root)
            service_info = systemd_services.get(host_config, {})
            annotated = tunnel_annotations.get(tunnel_id, {})
            display_name = (
                annotated.get("name")
                or service_info.get("tunnel_name")
                or (path.stem if path.stem != "config" else tunnel_id[:8])
            )
            tunnels.append(
                {
                    "id": tunnel_id,
                    "name": display_name,
                    "config_file": host_config,
                    "systemd_service": service_info.get("service"),
                    "status": "up" if host_config in running_configs else "unknown",
                    "ingress": parsed.get("ingress") or [],
                }
            )
        return tunnels

    def _npm_routes(self) -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        nginx_dir = self.settings.npm_nginx_dir
        proxy_dir = nginx_dir / "proxy_host"
        if proxy_dir.exists():
            for path in sorted(proxy_dir.glob("*.conf")):
                source = _host_path(path, self.settings.host_root)
                routes.extend(parse_npm_config(_read_text(path), source))
        custom = nginx_dir / "custom/http.conf"
        if custom.exists():
            routes.extend(parse_npm_config(_read_text(custom), _host_path(custom, self.settings.host_root)))
        return routes

    def _build_graph(
        self,
        *,
        annotations: dict[str, Any],
        docker_data: dict[str, Any],
        tunnels: list[dict[str, Any]],
        npm_routes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        warnings: list[dict[str, Any]] = list(annotations.get("warnings") or [])

        def add_node(
            node_id: str,
            node_type: str,
            display_name: str,
            status: str = "unknown",
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            existing = nodes.get(node_id)
            if existing:
                existing["metadata"].update(metadata or {})
                if existing.get("status") == "unknown" and status != "unknown":
                    existing["status"] = status
                return existing
            node = {
                "id": node_id,
                "type": node_type,
                "display_name": display_name,
                "status": status,
                "metadata": metadata or {},
            }
            nodes[node_id] = node
            return node

        def add_edge(
            source: str,
            target: str,
            edge_type: str,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            key = (source, target, edge_type)
            edges.setdefault(
                key,
                {
                    "id": f"{source}->{target}:{edge_type}",
                    "source": source,
                    "target": target,
                    "type": edge_type,
                    "metadata": metadata or {},
                },
            )

        add_node("internet", "internet", "Internet", "healthy")
        add_node("cloudflare", "cloudflare", "Cloudflare", "unknown")
        add_edge("internet", "cloudflare", "ROUTES_TO")

        containers_by_name = docker_data.get("containers_by_name", {})
        networks_by_name = docker_data.get("networks_by_name", {})
        npm_container = containers_by_name.get("nginx-proxy-manager")
        npm_node_id = (
            f"container:{npm_container['name']}" if npm_container else "service:nginx-proxy-manager"
        )
        add_node(
            npm_node_id,
            "container" if npm_container else "service",
            "Nginx Proxy Manager",
            npm_container.get("node_status", "unknown") if npm_container else "unknown",
            self._container_metadata(npm_container) if npm_container else {},
        )

        for network in docker_data.get("networks", []):
            add_node(
                f"network:{network['name']}",
                "docker_network",
                network["name"],
                "healthy",
                {
                    "driver": network.get("driver"),
                    "scope": network.get("scope"),
                    "internal": network.get("internal"),
                    "member_containers": network.get("members", []),
                },
            )

        for container in docker_data.get("containers", []):
            container_id = f"container:{container['name']}"
            add_node(
                container_id,
                "container",
                container["name"],
                container.get("node_status", "unknown"),
                self._container_metadata(container),
            )
            for network_name in container.get("networks", []):
                add_edge(container_id, f"network:{network_name}", "MEMBER_OF")

        npm_by_hostname: dict[str, dict[str, Any]] = {}
        for route in npm_routes:
            npm_by_hostname[route["hostname"]] = route

        hostnames_seen: dict[str, str] = {}
        discovered_routes: list[dict[str, Any]] = []
        for tunnel in tunnels:
            tunnel_node = f"tunnel:{tunnel['id']}"
            tunnel_hostnames = [item.get("hostname", "") for item in tunnel.get("ingress", []) if item.get("hostname")]
            add_node(
                tunnel_node,
                "cloudflare_tunnel",
                tunnel.get("name") or tunnel["id"][:8],
                tunnel.get("status", "unknown"),
                {
                    "uuid": tunnel["id"],
                    "short_uuid": tunnel["id"][:8],
                    "config_file": tunnel.get("config_file"),
                    "systemd_service": tunnel.get("systemd_service"),
                    "hostnames": tunnel_hostnames,
                    "targets": sorted({item.get("service", "") for item in tunnel.get("ingress", []) if item.get("service")}),
                },
            )
            add_edge("cloudflare", tunnel_node, "ROUTES_TO")

            for ingress in tunnel.get("ingress", []):
                hostname = ingress.get("hostname")
                if not hostname:
                    continue
                if hostname in hostnames_seen:
                    warnings.append(
                        {
                            "kind": "duplicate_hostname",
                            "severity": "warning",
                            "message": f"{hostname} appears in multiple tunnels",
                            "metadata": {"first_tunnel": hostnames_seen[hostname], "second_tunnel": tunnel["id"]},
                        }
                    )
                hostnames_seen[hostname] = tunnel["id"]
                domain_node = f"domain:{hostname}"
                add_node(
                    domain_node,
                    "domain",
                    hostname,
                    tunnel.get("status", "unknown"),
                    {
                        "hostname": hostname,
                        "tunnel": tunnel.get("name"),
                        "tunnel_uuid": tunnel["id"],
                        "tunnel_service": ingress.get("service"),
                    },
                )
                add_edge(tunnel_node, domain_node, "ROUTES_TO", {"service": ingress.get("service")})

                route = npm_by_hostname.get(hostname)
                if not route:
                    warnings.append(
                        {
                            "kind": "npm_route_missing",
                            "severity": "warning",
                            "message": f"No NPM route found for {hostname}",
                        }
                    )
                    discovered_routes.append(
                        {
                            "id": f"route:{hostname}",
                            "hostname": hostname,
                            "tunnel_node_id": tunnel_node,
                            "domain_node_id": domain_node,
                            "npm_node_id": None,
                            "target_node_id": None,
                            "tunnel": tunnel.get("name"),
                            "tunnel_uuid": tunnel["id"],
                            "tunnel_service": ingress.get("service"),
                            "npm_route": None,
                            "private_networks": [],
                        }
                    )
                    continue

                add_edge(domain_node, npm_node_id, "ROUTES_TO")
                target_name = route["forward_host"]
                target = containers_by_name.get(target_name)
                target_node = (
                    f"container:{target_name}" if target else f"upstream:{target_name}:{route['forward_port']}"
                )
                if target:
                    add_node(
                        target_node,
                        "container",
                        target_name,
                        target.get("node_status", "unknown"),
                        self._container_metadata(target),
                    )
                    if "npm-proxy" not in target.get("networks", []):
                        warnings.append(
                            {
                                "kind": "target_not_on_npm_proxy",
                                "severity": "warning",
                                "message": f"{hostname} targets {target_name}, but it is not attached to npm-proxy",
                            }
                        )
                    if target.get("node_status") == "down":
                        warnings.append(
                            {
                                "kind": "target_down",
                                "severity": "critical",
                                "message": f"{hostname} targets stopped container {target_name}",
                            }
                        )
                else:
                    add_node(
                        target_node,
                        "upstream",
                        f"{target_name}:{route['forward_port']}",
                        "unknown",
                        {"target": target_name, "port": route["forward_port"]},
                    )
                    warnings.append(
                        {
                            "kind": "target_unknown",
                            "severity": "warning",
                            "message": f"{hostname} targets {target_name}, which is not a discovered container",
                        }
                    )
                add_edge(npm_node_id, target_node, "PROXIES_TO", {"hostname": hostname, "port": route["forward_port"]})

                private_networks = self._private_networks_for(target, networks_by_name)
                discovered_routes.append(
                    {
                        "id": f"route:{hostname}",
                        "hostname": hostname,
                        "tunnel_node_id": tunnel_node,
                        "domain_node_id": domain_node,
                        "npm_node_id": npm_node_id,
                        "target_node_id": target_node,
                        "tunnel": tunnel.get("name"),
                        "tunnel_uuid": tunnel["id"],
                        "tunnel_service": ingress.get("service"),
                        "npm_route": route,
                        "private_networks": private_networks,
                    }
                )

        direct_host_services = self._direct_host_services(
            annotations=annotations,
            containers=docker_data.get("containers", []),
            add_node=add_node,
            add_edge=add_edge,
        )
        for item in direct_host_services:
            if item["classification"] == "UNKNOWN" and item["scope"] == "public":
                warnings.append(
                    {
                        "kind": "unannotated_public_port",
                        "severity": "warning",
                        "message": f"Public host port {item['host_port']}/{item['protocol']} on {item['container']} is not annotated",
                    }
                )

        route_targets = {route["forward_host"] for route in npm_routes}
        for container in docker_data.get("containers", []):
            if "npm-proxy" not in container.get("networks", []):
                continue
            if container["name"] in {"nginx-proxy-manager"}:
                continue
            if container["name"] not in route_targets:
                warnings.append(
                    {
                        "kind": "npm_proxy_without_route",
                        "severity": "warning",
                        "message": f"{container['name']} is on npm-proxy but no NPM route targets it",
                    }
                )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "nodes": sorted(nodes.values(), key=lambda item: (item["type"], item["display_name"])),
            "edges": sorted(edges.values(), key=lambda item: item["id"]),
            "routes": sorted(discovered_routes, key=lambda item: item["hostname"]),
            "direct_host_services": direct_host_services,
            "warnings": warnings,
            "sources": {
                "docker": {"available": docker_data.get("available"), "error": docker_data.get("error")},
                "cloudflared_configs": len(tunnels),
                "npm_routes": len(npm_routes),
            },
        }

    def _container_metadata(self, container: dict[str, Any] | None) -> dict[str, Any]:
        if not container:
            return {}
        return {
            "name": container.get("name"),
            "status": container.get("status"),
            "health": container.get("health"),
            "image": container.get("image"),
            "uptime_seconds": container.get("uptime_seconds"),
            "restart_count": container.get("restart_count"),
            "docker_networks": container.get("networks", []),
            "exposed_ports": container.get("exposed_ports", []),
            "published_ports": container.get("published_ports", []),
            "compose": container.get("compose", {}),
        }

    def _private_networks_for(
        self,
        container: dict[str, Any] | None,
        networks_by_name: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not container:
            return []
        private: list[dict[str, Any]] = []
        for network_name in container.get("networks", []):
            if network_name == "npm-proxy":
                continue
            network = networks_by_name.get(network_name)
            members = [name for name in (network or {}).get("members", []) if name != container["name"]]
            private.append(
                {
                    "network_node_id": f"network:{network_name}",
                    "name": network_name,
                    "members": [f"container:{name}" for name in members],
                }
            )
        return sorted(private, key=lambda item: item["name"])

    def _direct_host_services(
        self,
        *,
        annotations: dict[str, Any],
        containers: list[dict[str, Any]],
        add_node: Any,
        add_edge: Any,
    ) -> list[dict[str, Any]]:
        direct_annotations = annotations.get("direct_host_services") or {}
        grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for container in containers:
            for port in container.get("published_ports", []):
                key = (
                    container["name"],
                    port["host_port"],
                    port["protocol"],
                    port["container_port"],
                )
                item = grouped.setdefault(
                    key,
                    {
                        "id": f"direct:{container['name']}:{port['host_port']}/{port['protocol']}",
                        "container": container["name"],
                        "container_node_id": f"container:{container['name']}",
                        "host_ips": [],
                        "host_port": port["host_port"],
                        "container_port": port["container_port"],
                        "protocol": port["protocol"],
                        "status": container.get("node_status", "unknown"),
                    },
                )
                if port["host_ip"] not in item["host_ips"]:
                    item["host_ips"].append(port["host_ip"])

        direct_services: list[dict[str, Any]] = []
        for item in grouped.values():
            annotation = self._direct_annotation(direct_annotations, item)
            scope = "local" if all(_is_loopback(ip) for ip in item["host_ips"]) else "public"
            classification = annotation.get("classification") or (
                "LOCAL_ONLY" if scope == "local" else "UNKNOWN"
            )
            if item["container"] == "nginx-proxy-manager" and item["host_port"] in {"80", "443"}:
                classification = annotation.get("classification") or "PUBLIC_WEB"
            item.update(
                {
                    "name": annotation.get("name")
                    or f"{item['container']}:{item['host_port']}/{item['protocol']}",
                    "purpose": annotation.get("purpose") or "",
                    "classification": classification,
                    "scope": scope,
                    "host_ips": sorted(item["host_ips"]),
                }
            )
            port_node = f"host_port:{item['host_port']}/{item['protocol']}:{item['container']}"
            add_node(
                port_node,
                "host_port",
                f":{item['host_port']}/{item['protocol']}",
                item["status"],
                {
                    "host_ips": item["host_ips"],
                    "container": item["container"],
                    "container_port": item["container_port"],
                    "classification": item["classification"],
                    "purpose": item["purpose"],
                },
            )
            add_edge(item["container_node_id"], port_node, "EXPOSES")
            item["node_id"] = port_node
            direct_services.append(item)
        return sorted(direct_services, key=lambda item: (item["host_port"], item["protocol"], item["container"]))

    def _direct_annotation(self, annotations: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
        candidates = [
            f"{item['container']}:{item['host_port']}/{item['protocol']}",
            f"{item['container']}:{item['container_port']}/{item['protocol']}",
            f"{item['host_port']}/{item['protocol']}",
        ]
        for key in candidates:
            value = annotations.get(key)
            if isinstance(value, dict):
                return value
        return {}
