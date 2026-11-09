"""Background worker: the only process that talks to devices.

Loops: adapter polling + push subscriptions, command queue, climate engine, health/alerts,
heartbeat and scheduled backups. Every loop is crash-only: an exception is logged, the
loop sleeps with backoff and continues. At startup no device commands are sent until the
unit state has been read back (no surprise switching after a power cut).

Layout (one mixin per concern, assembled in ``core.Worker``):

- ``core``         lifecycle, loop runner, LISTEN, heartbeat, adapter loading/polling/subscribing
- ``ingest``       adapter events -> devices, readings, Hue scenes, button presses
- ``commands``     command queue -> adapters
- ``climate_loop`` climate engine rounds
- ``health``       sensor staleness alerts
- ``backup_job``   scheduled automatic backups
- ``aggregates``   (reserved) hourly energy aggregation and retention
"""

from .core import Worker, run

__all__ = ["Worker", "run"]
