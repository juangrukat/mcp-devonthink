"""Shared tool contract metadata helpers for DEVONthink MCP."""

from __future__ import annotations

from typing import Any


def build_description(
    *,
    summary: str,
    use_when: str,
    identifier_guidance: str,
    safety_class: str,
    prefer_when: str,
    example: str,
    degradation_contract: str | None = None,
) -> str:
    lines = [
        summary.strip(),
        f"Use when: {use_when.strip()}",
        f"Identifiers: {identifier_guidance.strip()}",
        f"Safety: {safety_class.strip()}",
        f"Prefer this when: {prefer_when.strip()}",
    ]
    if degradation_contract:
        lines.append(f"Degradation: {degradation_contract.strip()}")
    lines.append(f"Example: {example.strip()}")
    return " ".join(lines)


def catalog_entry(
    *,
    name: str,
    description: str,
    group: str,
    tier: str,
    status: str,
    canonical_tool: str,
    overlap_family: str | None,
    source_path: str,
    catalog_path: str,
    executable: str,
    priority: int,
    default_exposed: bool,
    accepted_identifiers: list[str],
    preferred_identifier: str | None,
    identifier_guidance: str,
    safety_class: str,
    profile_availability: list[str],
    prefer_when: str,
    example: str,
    degradation_contract: str | None = None,
    tags: list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
    invocation_pitfalls: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "group": group,
        "tier": tier,
        "status": status,
        "canonical_tool": canonical_tool,
        "overlap_family": overlap_family,
        "source_path": source_path,
        "catalog_path": catalog_path,
        "executable": executable,
        "priority": priority,
        "default_exposed": default_exposed,
        "accepted_identifiers": accepted_identifiers,
        "preferred_identifier": preferred_identifier,
        "identifier_guidance": identifier_guidance,
        "safety_class": safety_class,
        "profile_availability": profile_availability,
        "prefer_when": prefer_when,
        "degradation_contract": degradation_contract,
        "example": example,
        "tags": tags or [],
        "input_schema": input_schema or {},
        "invocation_pitfalls": invocation_pitfalls or [],
    }
