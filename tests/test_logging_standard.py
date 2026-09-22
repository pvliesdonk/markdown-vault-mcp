"""First-party log calls follow the family log-call grammar (template-owned).

The ``logging-standard`` skill writes every first-party message as
``event_name key=%s``: a snake_case event name, then ``name=value`` fields,
so a log consumer recovers typed fields from ``record.msg`` and
``record.args`` instead of an opaque sentence.  ``fastmcp-pvl-core`` owns
that grammar and ships the check; this test runs it over ``src/`` so a
non-conforming call fails the build rather than reaching a release
(template #611).

The check reads source with ``ast`` and imports nothing.  It judges level
calls (``debug`` .. ``critical``) on a module-level name bound by
``logging.getLogger(...)``, which is the only form the skill allows.  A
logger reached as ``self.logger`` or imported from another module, and
``logger.log(level, ...)``, are outside what it can see. That is a limit of
the check, not permission to write those forms.

The path is anchored to this file, never to the working directory: pvl-core
raises ``NotADirectoryError`` for a missing root, so a wrong path fails
loudly instead of passing having scanned nothing.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp_pvl_core import find_nonconforming_log_calls

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


def test_first_party_log_calls_follow_the_grammar() -> None:
    violations = find_nonconforming_log_calls(SRC)
    report = "\n".join(
        f"  {v.path.relative_to(REPO_ROOT)}:{v.line}: {v.reason}"
        + (f" template={v.template!r}" if v.template is not None else "")
        for v in violations
    )
    assert not violations, (
        f"{len(violations)} log call(s) do not follow the logging standard "
        "(event_name key=%s, one field per placeholder, no prose):\n"
        f"{report}\n"
        "See the logging-standard skill's Message Format section for the "
        "grammar and how to rewrite each case."
    )
