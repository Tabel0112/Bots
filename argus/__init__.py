"""ARGUS controller: interprets a request, plans it, runs it and publishes one result.

Phase 0 of the package is the contract layer that every later stage imports:

* :mod:`argus.contracts` - the messages passed between stages, with strict JSON
  round-trips.
* :mod:`argus.interfaces` - the Toolbox, Moderator and Ghost protocols the
  controller calls out through.
* :mod:`argus.registry` - the supported sites, operations and parameters.
* ``argus/examples/`` - one JSON fixture per message, used by the tests and as
  the shared handoff samples for the other workstreams.

The interpreter, planner, controller, store and fakes arrive in later phases.
See docs/hackathon/ARGUS-IMPLEMENTATION.md.
"""

from argus.contracts import SCHEMA_VERSION, ContractError

__all__ = ["SCHEMA_VERSION", "ContractError"]
