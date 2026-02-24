"""
Context Node Registry - Atomic Data Fetchers for Dynamic Context Assembly

This module implements a decorator-based registry system where individual data points
are registered as async functions ("nodes"). The Producer AI selects which nodes to execute,
and the registry fetches them in parallel using asyncio.gather.

This is RAG for live system state.
"""

import asyncio
import time
from typing import Dict, List, Callable, Any
from services import log_service

class ContextNodeRegistry:

    def __init__(self):
        self._nodes: Dict[str, Callable] = {}
        self._descriptions: Dict[str, str] = {}
        self._costs: Dict[str, str] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, cost: str = "low", visible: bool = True):
        def decorator(func: Callable):
            self._nodes[name] = func

            if visible:
                self._descriptions[name] = description

            self._costs[name] = cost
            self._stats[name] = {
                "times_requested": 0,
                "total_execution_time": 0,
                "failures": 0,
                "avg_execution_time": 0
            }
            return func
        return decorator

    async def fetch_nodes(self, node_keys: List[str], **kwargs) -> Dict[str, str]:
        start_time = time.perf_counter()

        valid_tasks = []
        valid_keys = []
        invalid_keys = []

        for key in node_keys:
            if key in self._nodes:
                valid_tasks.append(self._execute_node_with_stats(key, **kwargs))
                valid_keys.append(key)
                self._stats[key]["times_requested"] += 1
            else:
                invalid_keys.append(key)

        if invalid_keys:
            log_service.warning(f"[NODE REGISTRY] Invalid node keys requested: {invalid_keys}")

        log_service.node_registry(f"⚡ Executing {len(valid_tasks)} nodes in parallel")
        results = await asyncio.gather(*valid_tasks, return_exceptions=True)

        output = {}
        for key, result in zip(valid_keys, results):
            if isinstance(result, Exception):
                log_service.error(f"[NODE] '{key}' failed: {result}")
                self._stats[key]["failures"] += 1
                output[key] = ""
            else:
                output[key] = result or ""

        elapsed = time.perf_counter() - start_time
        log_service.node_performance(
            f"⚡ Fetched {len(valid_keys)} nodes in {elapsed*1000:.1f}ms (avg {elapsed*1000/len(valid_keys):.1f}ms/node)"
        )

        return output

    async def _execute_node_with_stats(self, key: str, **kwargs) -> str:
        start = time.perf_counter()

        try:
            result = await self._nodes[key](**kwargs)
            elapsed = time.perf_counter() - start

            stats = self._stats[key]
            stats["total_execution_time"] += elapsed
            stats["avg_execution_time"] = (
                stats["total_execution_time"] / stats["times_requested"]
            )

            return result

        except Exception as e:
            elapsed = time.perf_counter() - start
            log_service.error(f"[NODE REGISTRY] Error in node '{key}' after {elapsed*1000:.1f}ms: {e}")
            raise

    def get_menu_for_ai(self) -> str:
        menu_items = []
        for key in sorted(self._descriptions.keys()):
            desc = self._descriptions[key]
            cost = self._costs[key].upper()
            menu_items.append(f"- {key}: {desc} [{cost}]")

        return "\n".join(menu_items)

    def get_available_nodes(self) -> List[str]:
        return list(self._nodes.keys())

    def get_node_stats(self) -> Dict[str, Dict]:
        return self._stats.copy()

    def get_registry_info(self) -> Dict:
        cost_breakdown = {"low": 0, "medium": 0, "high": 0}
        for cost in self._costs.values():
            cost_breakdown[cost] = cost_breakdown.get(cost, 0) + 1

        total_requests = sum(s["times_requested"] for s in self._stats.values())
        total_failures = sum(s["failures"] for s in self._stats.values())

        sorted_by_usage = sorted(
            self._stats.items(),
            key=lambda x: x[1]["times_requested"],
            reverse=True
        )
        most_used = sorted_by_usage[:5] if sorted_by_usage else []

        sorted_by_speed = sorted(
            [(k, v) for k, v in self._stats.items() if v["times_requested"] > 0],
            key=lambda x: x[1]["avg_execution_time"],
            reverse=True
        )
        slowest = sorted_by_speed[:5] if sorted_by_speed else []

        return {
            "total_nodes": len(self._nodes),
            "cost_breakdown": cost_breakdown,
            "total_requests": total_requests,
            "total_failures": total_failures,
            "failure_rate": total_failures / total_requests if total_requests > 0 else 0,
            "most_used_nodes": [(k, v["times_requested"]) for k, v in most_used],
            "slowest_nodes": [(k, f"{v['avg_execution_time']*1000:.1f}ms") for k, v in slowest]
        }

node_registry = ContextNodeRegistry()

# Import context_nodes to trigger decorator registrations
from services_radio import context_nodes  # noqa: F401,E402