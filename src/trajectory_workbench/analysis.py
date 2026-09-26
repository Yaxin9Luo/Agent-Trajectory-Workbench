from __future__ import annotations

import json
from typing import Any

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient


class TrajectoryAnalyzer:
    def __init__(self, client: TypeSafeClient | None = None) -> None:
        self.client = client or TypeSafeClient()

    def analyze_run(self, normalized: Any) -> dict[str, Any]:
        run_state = self._run_state(normalized)
        run_answers = self._ask(
            run_state,
            {
                "health_score": Score(
                    instructions="Rate the overall health of this agent run from 0 to 10, considering task completion, error recovery, and efficiency.",
                    criteria=["failed", "poor", "fair", "good", "excellent"],
                ),
                "needs_human": Noul(
                    instructions="Does this run require human intervention to reach a satisfactory outcome?",
                ),
                "error_severity": Choice(
                    instructions="Classify the most severe error pattern in this run.",
                    criteria={
                        "none": "No errors occurred",
                        "recoverable": "Errors occurred but the agent recovered autonomously",
                        "persistent": "Errors persisted and the agent could not fully recover",
                        "fatal": "Errors caused complete task failure",
                    },
                ),
            },
        )

        error_tools = [
            tool for tool in normalized.tools
            if (tool.get("result") or {}).get("is_error")
        ]
        tool_answers = {}
        if error_tools:
            tool_state = self._error_tools_state(error_tools)
            tool_answers = self._ask(
                tool_state,
                {
                    "failure_category": Choice(
                        instructions="Classify the primary failure pattern across these tool errors.",
                        criteria={
                            "validation": "Input validation or constraint violations",
                            "tool_misuse": "Incorrect tool arguments or usage",
                            "environment": "Missing files, paths, or environment setup",
                            "logic": "Flawed agent logic or reasoning",
                            "external": "External service or API failures",
                        },
                    ),
                },
            )

        return {
            "run": self._extract_run_answers(run_answers),
            "errors": self._extract_tool_answers(tool_answers),
            "tool_error_count": len(error_tools),
        }

    def _ask(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        response = self.client.system_one(state=state, questions=questions)
        return {
            name: answer
            for name, answer in response.answers.items()
        }

    def _run_state(self, normalized: Any) -> dict[str, Any]:
        metrics = normalized.metrics
        return {
            "tool_calls": metrics.get("tool_calls", 0),
            "tool_kinds": metrics.get("tool_kinds", 0),
            "message_count": metrics.get("message_count", 0),
            "artifact_state_count": metrics.get("artifact_state_count", 0),
            "workbench_calls": metrics.get("workbench_calls", 0),
            "total_cost_usd": metrics.get("total_cost_usd"),
            "max_offset_ms": metrics.get("max_offset_ms", 0),
            "runtime_classification": normalized.runtime.get("classification", "unknown"),
            "runtime_failure_message": normalized.runtime.get("failure_message"),
            "process_terminal_reason": normalized.runtime.get("process_terminal_reason"),
            "tool_error_count": sum(
                1 for t in normalized.tools
                if (t.get("result") or {}).get("is_error")
            ),
            "tool_names": [t["name"] for t in normalized.tools[:20]],
            "tool_results_sample": [
                {
                    "name": t["name"],
                    "is_error": (t.get("result") or {}).get("is_error", False),
                    "result_text": (t.get("result") or {}).get("text", "")[:300],
                }
                for t in normalized.tools[:10]
            ],
            "workbench_diagnostics": [
                {
                    "status": (wb.get("observation") or {}).get("status"),
                    "error_code": (wb.get("observation") or {}).get("error_code"),
                    "diagnostics": (wb.get("observation") or {}).get("diagnostics"),
                }
                for wb in normalized.workbench
            ],
        }

    def _error_tools_state(self, error_tools: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "error_count": len(error_tools),
            "errors": [
                {
                    "tool_name": t["name"],
                    "tool_input": json.dumps(t.get("input", {}), ensure_ascii=False)[:300],
                    "result_text": (t.get("result") or {}).get("text", "")[:300],
                }
                for t in error_tools[:10]
            ],
        }

    def _extract_run_answers(self, answers: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "health_score" in answers:
            score = answers["health_score"]
            result["health_score"] = {
                "score": getattr(score, "score", None),
                "confidence": getattr(score, "confidence", None),
                "probabilities": getattr(score, "probabilities", None),
            }
        if "needs_human" in answers:
            noul = answers["needs_human"]
            result["needs_human"] = {
                "probability": getattr(noul, "noul", None),
            }
        if "error_severity" in answers:
            choice = answers["error_severity"]
            result["error_severity"] = {
                "choice": getattr(choice, "choice", None),
                "confidence": getattr(choice, "confidence", None),
                "probabilities": getattr(choice, "probabilities", None),
            }
        return result

    def _extract_tool_answers(self, answers: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "failure_category" in answers:
            choice = answers["failure_category"]
            result["failure_category"] = {
                "choice": getattr(choice, "choice", None),
                "confidence": getattr(choice, "confidence", None),
                "probabilities": getattr(choice, "probabilities", None),
            }
        return result
