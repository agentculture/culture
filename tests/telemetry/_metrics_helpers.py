"""Helpers for asserting on InMemoryMetricReader output in metrics tests.

reader.get_metrics_data() returns a MetricsData object whose nested
structure is resource_metrics → scope_metrics → metrics → data points.
These helpers walk it for the common assertions we need."""

from __future__ import annotations

from typing import Any


def _walk_data_points(reader, name: str):
    """Yield every data point across all resources/scopes for a metric `name`."""
    data = reader.get_metrics_data()
    if data is None:
        return
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    yield from metric.data.data_points


def _attrs_match(point_attrs: dict, expected: dict) -> bool:
    return all(point_attrs.get(k) == v for k, v in expected.items())


def get_counter_value(reader, name: str, **attrs: Any) -> float:
    """Sum of counter values for points matching `attrs`. 0.0 if absent."""
    total = 0.0
    for point in _walk_data_points(reader, name):
        if _attrs_match(dict(point.attributes), attrs):
            total += point.value
    return total


def get_up_down_value(reader, name: str, **attrs: Any) -> float:
    """Latest UpDownCounter value for points matching `attrs`. 0.0 if absent."""
    for point in _walk_data_points(reader, name):
        if _attrs_match(dict(point.attributes), attrs):
            return point.value
    return 0.0


def get_histogram_count(reader, name: str, **attrs: Any) -> int:
    """Count of recorded values for histogram points matching `attrs`."""
    total = 0
    for point in _walk_data_points(reader, name):
        if _attrs_match(dict(point.attributes), attrs):
            total += point.count
    return total
