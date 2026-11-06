"""Shared synthetic outfit workflow; contains TEST assets only."""

from __future__ import annotations


def graph() -> dict:
    nodes = {
        "137": ("LoadImage", {"image": "TEST_image.png"}),
        "155": ("VHS_LoadVideo", {"video": "TEST_reference.mp4"}),
        "132": ("PrimitiveFloat", {"value": 5.0}),
        "181": ("PrimitiveInt", {"value": 24}),
        "167": (
            "ImageResizeKJv2",
            {
                "width": 576,
                "height": 1024,
                "keep_proportion": "crop",
                "crop_position": "center",
                "divisible_by": 32,
            },
        ),
        "136": (
            "MiniMaxH3ReferenceToVideo",
            {"width": ["167", 1], "height": ["167", 2], "prompt": ["173", 0]},
        ),
        "129": ("Seed", {"noise_seed": 1}),
        "180": ("Seed", {"seed": 100001}),
        "173": ("Text", {"value": "TEST_outfit"}),
        "174": ("Text", {"value": "TEST_identity"}),
        "185": ("Text", {"value": "TEST_description"}),
        "131": (
            "ComfyMathExpression",
            {
                "expression": "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17",
                "values.a": ["132", 0],
            },
        ),
        "157": ("ComfyMathExpression", {"expression": "a*24", "values.a": ["132", 0]}),
        "182": ("DF_Int_to_Float", {"Value": ["181", 0]}),
        "150": ("VHS_VideoCombine", {"frame_rate": ["182", 0]}),
        "188": ("VHS_VideoCombine", {"frame_rate": 24}),
    }
    return {
        key: {"class_type": kind, "inputs": inputs}
        for key, (kind, inputs) in nodes.items()
    }
