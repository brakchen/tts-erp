"""Passive diagnostic value coverage, not native or guard acceptance proof."""
from __future__ import annotations

import pytest

from tests.scripts import test_frontend_remaining_upload_observations as native
from tests.support.frontend_p2_browser import BrowserFence

pytestmark = pytest.mark.domain_frontend


@pytest.mark.parametrize(('value', 'expected'), [
    ('net::ERR_ABORTED', 'ABORTED'),
    ('net::ERR_FAILED', 'FAILED'),
    ('net::ERR_BLOCKED_BY_RESPONSE', 'BLOCKED_BY_RESPONSE'),
    ('cors', 'CORS_BLOCKED'),
    ('mixed-content', 'MIXED_CONTENT'),
    ('inspector', 'INSPECTOR_BLOCKED'),
    ('net::ERR_CONNECTION_REFUSED', 'CONNECTION_REFUSED'),
    ('net::ERR_CONNECTION_RESET', 'CONNECTION_RESET'),
    (None, 'NONE'),
    ('TEST_unclassified_failure', 'OTHER'),
])
def test_failure_label_is_closed(value, expected):
    assert native._failure_label(value) == expected


def test_unknown_failure_canary_is_not_returned_or_logged(capsys):
    label = native._failure_label('TEST_nonsecret_unknown_canary')
    assert label == 'OTHER'
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ''


def allocate_counter_values():
    # Value allocator only: never construct/bypass BrowserFence or a DB fixture.
    factory = BrowserFence.__dataclass_fields__['native_stages'].default_factory
    assert callable(factory)
    return factory()


def test_stage_default_has_exact_six_zero_integers():
    values = allocate_counter_values()
    assert values == {'entry': 0, 'bound': 0, 'header_requested': 0,
                      'header_read': 0, 'accepted': 0, 'continued': 0}
    assert all(type(value) is int for value in values.values())


def test_stage_default_is_per_instance_value():
    first = allocate_counter_values()
    second = allocate_counter_values()
    first['entry'] += 1
    assert first['entry'] == 1
    assert second['entry'] == 0
    assert first is not second
