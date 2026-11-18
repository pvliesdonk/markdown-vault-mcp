"""Parsing operator-supplied URLs without repeating them back.

Ported from ``fastmcp_pvl_core._url`` 10.2.0 (#2279); only the parsers the
key-value store needs came along.

Every URL an operator sets may carry credentials (``user:pass@`` in the
userinfo, a pre-signed token in the query string), so nothing derived from
one may reach a log line or an exception message unredacted.

The trap this module closes is that :func:`urllib.parse.urlparse` itself
leaks. CPython's ``_checknetloc`` interpolates the raw netloc into its
``ValueError``::

    >>> urlparse("redis://alice:hunter2@h℀st")
    ValueError: netloc 'alice:hunter2@h℀st' contains invalid
    characters under NFKC normalization

(``℀`` NFKC-normalises to ``a/c``, so ``_checknetloc`` sees a delimiter.)
A site that lets that escape publishes the password; so does one that
chains it with ``from exc``, because the rendered traceback prints the
cause's message too. Only ``from None`` with a message of our own is safe.

Two shapes, because the two contexts have opposite requirements:

- :func:`parse_operator_url` raises :class:`ConfigurationError`, because
  startup configuration must fail fast.
- :func:`try_parse_url` never raises, for log lines on a path that has not
  validated the URL yet, where raising would emit the unredacted value.
"""

from __future__ import annotations

from urllib.parse import ParseResult, urlparse

from markdown_vault_mcp._pvl_core import ConfigurationError

UNPARSEABLE = "<unparseable>"
"""Stand-in for a URL that could not be parsed, for logs and redactions."""


def parse_operator_url(url: str, *, variable: str) -> ParseResult:
    """Parse an operator-supplied *url*, or reject it as misconfiguration.

    Args:
        url: The operator's value. Never appears in the raised message.
        variable: Display name of the setting it came from, so the
            operator knows which value to fix.

    Returns:
        The parsed URL.

    Raises:
        ConfigurationError: *url* does not parse. The message names
            *variable* only, and the cause is suppressed with ``from None``
            because ``urlparse``'s own message can embed the netloc.
    """
    try:
        return urlparse(url)
    except ValueError:
        raise ConfigurationError(
            f"{variable} could not be parsed as a URL. Its value is withheld "
            "here because it may carry credentials; check the value you set."
        ) from None


def try_parse_url(url: str) -> ParseResult | None:
    """Parse *url*, or return ``None`` if it does not parse.

    Args:
        url: Any URL, trusted or not.

    Returns:
        The parsed URL, or ``None`` when it does not parse. The
        ``ValueError`` is swallowed, because its message is what leaks.
    """
    try:
        return urlparse(url)
    except ValueError:
        return None
