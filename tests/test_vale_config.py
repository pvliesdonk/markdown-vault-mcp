"""Vale configuration contract (template-owned).

`.vale.ini` is seeded once, so a template change to it reaches this project
only by hand. The one line that matters for template prose is the
vocabulary activation: the template's own accepted terms live in the
re-rendered `vocabularies/Template/accept.txt` and are inert until
`.vale.ini` lists that layer (#366). This test turns the silent gap into a
named failure the first time the update lands.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_vale_ini_activates_the_template_vocabulary() -> None:
    ini = (REPO_ROOT / ".vale.ini").read_text(encoding="utf-8")
    m = re.search(r"^Vocab\s*=\s*(.+)$", ini, re.M)
    assert m, ".vale.ini has no `Vocab =` line"
    layers = {v.strip() for v in m.group(1).split(",")}
    assert "Template" in layers, (
        f"`Vocab = {m.group(1).strip()}` does not list the template layer; set it to "
        "`Vocab = Base, Template` so terms the template's prose needs "
        "(vocabularies/Template/accept.txt, re-rendered on every update) are accepted"
    )
    assert (
        REPO_ROOT / ".vale/styles/config/vocabularies/Template/accept.txt"
    ).is_file()
