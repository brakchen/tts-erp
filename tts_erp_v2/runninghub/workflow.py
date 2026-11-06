"""Versioned mapping for the verified outfit workflow, never an editor graph."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

WORKFLOW_ID = "2111762608670990337"
MAX_SEED = 2**53 - 1 - 100000
SPEC = {"width": 576, "height": 1024, "fps": 24, "frames": 120, "seconds": 5.0}
FPS_EXPRESSIONS = {
    "131": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
    "157": "a*24",
}


class WorkflowError(ValueError):
    pass


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def validate_graph(value: Any, workflow_id: str = WORKFLOW_ID) -> dict[str, Any]:
    if workflow_id != WORKFLOW_ID:
        raise WorkflowError("WORKFLOW_MAPPING_REQUIRED")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise WorkflowError("INVALID_WORKFLOW_JSON") from None
    if (
        not isinstance(value, dict)
        or not value
        or any(
            not isinstance(key, str)
            or not isinstance(node, dict)
            or not isinstance(node.get("class_type"), str)
            or not isinstance(node.get("inputs"), dict)
            for key, node in value.items()
        )
    ):
        raise WorkflowError("API_GRAPH_REQUIRED")
    expected = {
        "137": ("LoadImage", {"image": str}),
        "155": ("VHS_LoadVideo", {"video": str}),
        "132": ("PrimitiveFloat", {"value": (int, float)}),
        "181": ("PrimitiveInt", {"value": int}),
        "167": (
            "ImageResizeKJv2",
            {
                "width": int,
                "height": int,
                "keep_proportion": str,
                "crop_position": str,
                "divisible_by": int,
            },
        ),
        "136": (
            "MiniMaxH3ReferenceToVideo",
            {"width": list, "height": list, "prompt": list},
        ),
        "129": (None, {"noise_seed": int}),
        "180": (None, {"seed": int}),
        "173": (None, {"value": str}),
        "174": (None, {"value": str}),
        "185": (None, {"value": str}),
        "150": ("VHS_VideoCombine", {}),
        "188": ("VHS_VideoCombine", {}),
    }
    for node_id, (class_type, fields) in expected.items():
        node = value.get(node_id, {})
        if class_type and node.get("class_type") != class_type:
            raise WorkflowError(f"WORKFLOW_MAPPING_REQUIRED: node {node_id}")
        for field, kind in fields.items():
            item = node.get("inputs", {}).get(field)
            if isinstance(item, bool) or not isinstance(item, kind):
                raise WorkflowError(f"WORKFLOW_MAPPING_REQUIRED: {node_id}.{field}")
    for field, expected_link in (("width", ["167", 1]), ("height", ["167", 2])):
        if value["136"]["inputs"][field] != expected_link:
            raise WorkflowError(f"WORKFLOW_MAPPING_REQUIRED: 136.{field}")
    for node_id, expression in FPS_EXPRESSIONS.items():
        node = value.get(node_id, {})
        if (
            node.get("class_type") != "ComfyMathExpression"
            or node.get("inputs", {}).get("expression") != expression
            or node["inputs"].get("values.a") != ["132", 0]
        ):
            raise WorkflowError(f"WORKFLOW_MAPPING_REQUIRED: fps expression {node_id}")
    if (
        value.get("182", {}).get("inputs", {}).get("Value") != ["181", 0]
        or value["150"]["inputs"].get("frame_rate") != ["182", 0]
        or value["188"]["inputs"].get("frame_rate") != 24
    ):
        raise WorkflowError("WORKFLOW_MAPPING_REQUIRED: output fps")
    # Validate all links without rewriting their graph.
    for node in value.values():
        for item in node["inputs"].values():
            if (
                isinstance(item, list)
                and len(item) == 2
                and isinstance(item[0], str)
                and isinstance(item[1], int)
                and item[0] not in value
            ):
                raise WorkflowError("WORKFLOW_DANGLING_LINK")
    return copy.deepcopy(value)


def prepare(
    graph: dict[str, Any],
    image: str,
    video: str,
    seed: int,
    instructions: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = validate_graph(graph)
    if type(seed) is not int or not 0 <= seed <= MAX_SEED:
        raise WorkflowError("UNSAFE_SEED")
    changes = [
        ("137", "image", image),
        ("155", "video", video),
        ("129", "noise_seed", seed),
        ("180", "seed", seed + 100000),
    ]
    for node, field, value in changes:
        result[node]["inputs"][field] = value
    result["132"]["inputs"]["value"] = 5.0
    result["181"]["inputs"]["value"] = 24
    result["167"]["inputs"].update(
        width=576,
        height=1024,
        keep_proportion="crop",
        crop_position="center",
        divisible_by=32,
    )
    for node, value in instructions.items():
        if node not in {"173", "174", "185"} or not isinstance(value, str):
            raise WorkflowError("INVALID_INSTRUCTION")
        result[node]["inputs"]["value"] = value
    return result, [
        {"nodeId": n, "fieldName": f, "fieldValue": v} for n, f, v in changes
    ]
