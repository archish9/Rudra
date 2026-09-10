"""Sending a run's evidence somewhere the maintainer can reach it (OPEN-110).

`.rudra/run/logs/` is Rudra's whole support channel (CLAUDE.md §8a), and
it is a folder the user must find, read for secrets, and attach by hand.
This package is the other half: when a user configures Langfuse keys, the
same run reports itself to their Langfuse project as it goes, and the
maintainer can be given a live trace tree instead of a zip file.

Nothing here is on unless keys are configured, and nothing here may end a
run -- `memory/degrade.py`'s rule, for the same reason: an observability
backend that is down is not a reason to stop working.
"""

from rudra.telemetry.langfuse_sink import Telemetry, build_telemetry

__all__ = ["Telemetry", "build_telemetry"]
