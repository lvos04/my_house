"""Energy aggregation and data retention (verbeterlijst items 5 and 19).

This mixin will hold the worker's hourly energy aggregation: folding raw counter readings
(``sensor_readings``) into ``energy_hourly`` rows per device/metric/hour (kWh delta, first
and last counter value, sample count, quality flags such as suspect jumps or resets), plus
the retention job that deletes old raw data in batches according to
``Settings.retention_power_days`` / ``retention_counter_days`` / ``retention_log_days``
and prunes ``processed_events``.

It is intentionally empty for now and not yet scheduled in ``Worker.run()``; the energy
work adds the round method(s) here and registers the loop in ``core.Worker.run``.
"""

from __future__ import annotations


class AggregatesMixin:
    """Hourly energy aggregation and retention (to be implemented)."""
