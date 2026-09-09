"""Railway deployment provider.

Lunel's "cloud" provider for Railway (https://railway.com). Railway's
networking model shapes this provider:

* One public HTTP domain per service (``*.up.railway.app``), WebSocket-capable,
  TLS terminated at Railway's edge.
* Additional public endpoints are port-forwards (TCP proxies) — not needed by
  Lunel Core v1 since every supported protocol (VLESS / Trojan / Shadowsocks
  over WebSocket and xHTTP) rides on HTTP(S).
* Railway auto-injects RAILWAY_PROJECT_ID / RAILWAY_ENVIRONMENT_ID /
  RAILWAY_SERVICE_ID / RAILWAY_PUBLIC_DOMAIN into services running on it.

One Lunel instance = one Railway service in the Lunel project, deployed from
a container image (default ``ghcr.io/lunelsh/lunel-core:latest``), with a
generated public domain and its secret injected as a Railway variable.

API: GraphQL v2 at https://backboard.railway.app/graphql/v2 (workspace token).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from ..logging import get

log = get("runtime", "lunel.console.railway")

GRAPHQL_URL = "https://backboard.railway.app/graphql/v2"

# Railway deployment status -> Lunel deployment status
STATUS_MAP = {
    "QUEUED": "queued",
    "INITIALIZING": "preparing",
    "BUILDING": "building",
    "WAITING": "building",
    "DEPLOYING": "starting",
    "SUCCESS": "running",
    "FAILED": "failed",
    "CRASHED": "failed",
    "SLEEPING": "stopped",
    "REMOVED": "stopped",
    "REMOVING": "stopping",
    "SKIPPED": "failed",
    "NEEDS_APPROVAL": "queued",
}


class RailwayError(RuntimeError):
    pass


class RailwayProvider:
    def __init__(self, token: str, project_id: str, environment_id: str,
                 core_image: str = "ghcr.io/lunelsh/lunel-core:latest"):
        self.token = token
        self.project_id = project_id
        self.environment_id = environment_id
        self.core_image = core_image

    @classmethod
    def from_env(cls) -> "RailwayProvider":
        token = os.environ.get("LUNEL_RAILWAY_TOKEN", "")
        if not token:
            raise RailwayError("LUNEL_RAILWAY_TOKEN is not set")
        project = os.environ.get("LUNEL_RAILWAY_PROJECT_ID") or os.environ.get("RAILWAY_PROJECT_ID", "")
        environment = (
            os.environ.get("LUNEL_RAILWAY_ENVIRONMENT_ID") or os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
        )
        if not project or not environment:
            raise RailwayError(
                "LUNEL_RAILWAY_PROJECT_ID / LUNEL_RAILWAY_ENVIRONMENT_ID must be set "
                "(they are auto-injected when the Console itself runs on Railway)"
            )
        image = os.environ.get("LUNEL_CORE_IMAGE", "ghcr.io/lunelsh/lunel-core:latest")
        return cls(token, project, environment, image)

    @staticmethod
    def available() -> bool:
        return bool(
            os.environ.get("LUNEL_RAILWAY_TOKEN")
            and (
                os.environ.get("LUNEL_RAILWAY_PROJECT_ID")
                or os.environ.get("RAILWAY_PROJECT_ID")
            )
            and (
                os.environ.get("LUNEL_RAILWAY_ENVIRONMENT_ID")
                or os.environ.get("RAILWAY_ENVIRONMENT_ID")
            )
        )

    # ------------------------------------------------------------------ gql
    async def _gql(self, query: str, variables: dict[str, Any] | None = None,
                   timeout: float = 30.0) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code == 401:
            raise RailwayError("Railway token rejected (401) — check LUNEL_RAILWAY_TOKEN")
        if resp.status_code == 429:
            raise RailwayError("Railway API rate limited (429)")
        data = resp.json()
        if data.get("errors"):
            raise RailwayError(f"Railway API error: {data['errors'][0].get('message', '?')[:300]}")
        return data.get("data", {})

    # ------------------------------------------------------- instance launch
    async def create_service(self, name: str) -> str:
        data = await self._gql(
            """
            mutation ServiceCreate($input: ServiceCreateInput!) {
              serviceCreate(input: $input) { id }
            }
            """,
            {"input": {"projectId": self.project_id, "environmentId": self.environment_id, "name": name}},
        )
        return data["serviceCreate"]["id"]

    async def configure_service(self, service_id: str, core_api_token: str, public_host: str) -> None:
        await self._gql(
            """
            mutation InstanceUpdate($serviceId: String!, $environmentId: String!, $input: ServiceInstanceUpdateInput!) {
              serviceInstanceUpdate(serviceId: $serviceId, environmentId: $environmentId, input: $input) { id }
            }
            """,
            {
                "serviceId": service_id,
                "environmentId": self.environment_id,
                "input": {
                    "source": {"image": self.core_image},
                    "startCommand": "",
                    "healthcheckPath": "/health",
                    "restartPolicyType": "ON_FAILURE",
                    "restartPolicyMaxRetries": 5,
                },
            },
        )
        variables = {
            "LUNEL_CORE_API_TOKEN": core_api_token,
            "LUNEL_STATE_PATH": "/data/state.json",
            "PORT": "8000",
        }
        if public_host:
            variables["LUNEL_PUBLIC_HOST"] = public_host
        for name, value in variables.items():
            await self._gql(
                """
                mutation VariableUpsert($input: VariableUpsertInput!) {
                  variableUpsert(input: $input)
                }
                """,
                {
                    "input": {
                        "projectId": self.project_id,
                        "environmentId": self.environment_id,
                        "serviceId": service_id,
                        "name": name,
                        "value": value,
                        "skipDeploys": True,
                    }
                },
            )

    async def create_domain(self, service_id: str, target_port: int = 8000) -> dict:
        """Generate a public domain (WebSocket + HTTPS capable) for the service."""
        data = await self._gql(
            """
            mutation DomainCreate($input: ServiceDomainCreateInput!) {
              serviceDomainCreate(input: $input) { id domain targetPort }
            }
            """,
            {"input": {"environmentId": self.environment_id, "serviceId": service_id,
                       "targetPort": target_port}},
        )
        return data["serviceDomainCreate"]

    async def delete_domain(self, domain_id: str) -> None:
        await self._gql("mutation($id: String!) { serviceDomainDelete(id: $id) }", {"id": domain_id})

    async def trigger_deploy(self, service_id: str) -> str:
        data = await self._gql(
            """
            mutation Deploy($serviceId: String!, $environmentId: String!) {
              serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId) { id }
            }
            """,
            {"serviceId": service_id, "environmentId": self.environment_id},
        )
        return data["serviceInstanceDeployV2"]["id"]

    # ---------------------------------------------------------- status/logs
    async def latest_deployment(self, service_id: str) -> dict | None:
        data = await self._gql(
            """
            query($serviceId: String!, $environmentId: String!) {
              deployments(serviceId: $serviceId, environmentId: $environmentId, last: 1) {
                edges { node { id status createdAt staticUrl } }
              }
            }
            """,
            {"serviceId": service_id, "environmentId": self.environment_id},
        )
        edges = data.get("deployments", {}).get("edges") or []
        return edges[0]["node"] if edges else None

    async def deployment_logs(self, deployment_id: str, limit: int = 200) -> list[str]:
        data = await self._gql(
            """
            query($deploymentId: String!, $limit: Int!) {
              deploymentLogs(deploymentId: $deploymentId, limit: $limit)
            }
            """,
            {"deploymentId": deployment_id, "limit": limit},
        )
        logs = data.get("deploymentLogs")
        if isinstance(logs, str):
            return logs.splitlines()
        return logs or []

    async def service_meta(self, service_id: str) -> dict | None:
        data = await self._gql(
            """
            query($id: String!) { service(id: $id) { id name } }
            """,
            {"id": service_id},
        )
        return data.get("service")

    # ------------------------------------------------------------- lifecycle
    async def stop_service(self, service_id: str) -> None:
        dep = await self.latest_deployment(service_id)
        if dep and dep.get("id"):
            await self._gql(
                "mutation($id: String!) { deploymentStop(id: $id) }", {"id": dep["id"]}
            )

    async def start_service(self, service_id: str) -> str:
        return await self.trigger_deploy(service_id)

    async def restart_service(self, service_id: str) -> str:
        dep = await self.latest_deployment(service_id)
        if dep and dep.get("id"):
            data = await self._gql(
                "mutation($id: String!) { deploymentRedeploy(id: $id) }", {"id": dep["id"]}
            )
            # deploymentRedeploy returns Boolean in some schema versions
            if isinstance(data.get("deploymentRedeploy"), bool):
                return dep["id"]
        return await self.trigger_deploy(service_id)

    async def delete_service(self, service_id: str) -> None:
        await self._gql("mutation($id: String!) { serviceDelete(id: $id) }", {"id": service_id})
