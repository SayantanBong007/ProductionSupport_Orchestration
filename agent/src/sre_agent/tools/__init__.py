"""
Agent tools — raw observations from the running system.

Each specialist agent gets a toolset scoped to its domain; the incident
commander gets the incident memory plus its control tools (see agents.py).
"""

from sre_agent.tools.base import Tool
from sre_agent.tools.infrastructure import TOOLS as INFRA_TOOLS
from sre_agent.tools.memory import TOOLS as MEMORY_TOOLS
from sre_agent.tools.observability import TOOLS as OBS_TOOLS

LOGS_TOOLSET    = [OBS_TOOLS["search_logs"], INFRA_TOOLS["container_logs"]]
METRICS_TOOLSET = [OBS_TOOLS["list_metrics"], OBS_TOOLS["prometheus_query"],
                   OBS_TOOLS["prometheus_range"], OBS_TOOLS["check_service_health"]]
INFRA_TOOLSET   = [INFRA_TOOLS["list_containers"], INFRA_TOOLS["inspect_container"],
                   INFRA_TOOLS["docker_events"], INFRA_TOOLS["container_logs"]]
MEMORY_TOOLSET  = [MEMORY_TOOLS["search_past_incidents"]]

__all__ = ["Tool", "LOGS_TOOLSET", "METRICS_TOOLSET", "INFRA_TOOLSET", "MEMORY_TOOLSET",
           "OBS_TOOLS", "INFRA_TOOLS"]
