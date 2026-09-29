from __future__ import annotations

from tts_erp_v2.access import DeploymentPathInput, canonicalize_path


def test_canonicalize_path_restores_stripped_root_prefix() -> None:
    result = canonicalize_path(
        DeploymentPathInput(
            path="/v2/pages/manual-costs",
            raw_path=b"/v2/pages/manual-costs",
            root_path="/tts",
        )
    )

    assert result.downstream_path == "/tts/v2/pages/manual-costs"
    assert result.downstream_raw_path == b"/tts/v2/pages/manual-costs"
    assert result.route_path == "/v2/pages/manual-costs"
    assert result.root_path == "/tts"


def test_canonicalize_path_keeps_intact_root_prefix() -> None:
    result = canonicalize_path(
        DeploymentPathInput(
            path="/tts/v2/pages/manual-costs",
            raw_path=b"/tts/v2/pages/manual-costs",
            root_path="/tts",
        )
    )

    assert result.downstream_path == "/tts/v2/pages/manual-costs"
    assert result.route_path == "/v2/pages/manual-costs"


def test_canonicalize_path_maps_exact_root_to_route_root() -> None:
    result = canonicalize_path(
        DeploymentPathInput(path="/tts", raw_path=b"/tts", root_path="/tts")
    )

    assert result.downstream_path == "/tts"
    assert result.route_path == "/"


def test_canonicalize_path_does_not_treat_similar_prefix_as_root() -> None:
    result = canonicalize_path(
        DeploymentPathInput(
            path="/ttsx/v2/data",
            raw_path=b"/ttsx/v2/data",
            root_path="/tts",
        )
    )

    assert result.downstream_path == "/tts/ttsx/v2/data"
    assert result.route_path == "/ttsx/v2/data"


def test_canonicalize_path_preserves_absent_raw_path() -> None:
    result = canonicalize_path(
        DeploymentPathInput(path="/healthz", raw_path=None, root_path="")
    )

    assert result.downstream_path == "/healthz"
    assert result.downstream_raw_path is None
    assert result.route_path == "/healthz"
