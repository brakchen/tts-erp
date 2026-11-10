"""Browser-level guard: every readiness anchor really hits the canned page.

The source-level guards live in ``tests/support/test_page_readiness_contract.py``
(they run in the fast scope). This file proves the part only a browser can
show: the anchors in the contract table really match what the page renders.

The old helper's alert-page branch waited for ``#alert-status`` / ``#alert-rows``
— ids of the Jinja template P6 deleted — so it was permanently false and the
page fell through to a fixture-text anchor. These cases fail loudly when an
anchor no longer matches.
"""
from __future__ import annotations

import pytest
from render_support import page_path

from tests.support.page_readiness import CONTRACTS, wait_canned_page

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]

#: These two only render data rows with an explicit shop, so they use the
#: same fixture the shared layout gate uses.
_DATA_DRIVEN = ("spu-roi", "focused-spus")


@pytest.mark.parametrize("slug", sorted(set(CONTRACTS) - set(_DATA_DRIVEN)))
def test_contract_anchors_hit_the_canned_page(frontend_browser_renderer, slug) -> None:
    page = frontend_browser_renderer.open(f"/v2/pages/{slug}")
    wait_canned_page(page, slug)


@pytest.mark.parametrize("slug", _DATA_DRIVEN)
def test_data_driven_contract_anchors_hit(browser_renderer, slug) -> None:
    page = browser_renderer.open(page_path(slug, 7))
    wait_canned_page(page, slug)
