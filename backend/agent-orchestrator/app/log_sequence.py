"""Per-job monotonic sequence counter for job_logs ordering.

Each crew.kickoff() creates a fresh counter via new_counter() and stores it
on _thread_local.seq_counter (set in crew.py::_kickoff_with_context).

Both litellm_callbacks.py and crewai_tools.py read _thread_local.seq_counter
to stamp sequence numbers at the moment each event fires — before drain time —
so ORDER BY sequence_num ASC reflects true execution order regardless of which
queue is drained first.

Concurrent jobs are fully isolated: each runs in its own executor thread with
its own threading.local() slot, so counters never interfere.
"""
import itertools


def new_counter() -> itertools.count:
    """Return a fresh monotonic counter starting at 1."""
    return itertools.count(1)
