"""Parsing operator-supplied URLs without repeating them back.

Ported from ``fastmcp_pvl_core._url`` 10.2.0 (#2279, #2282, #2333): the
parsers the key-value store and ``_fetch`` need, and the free-text redactor
``get_server_info`` and the health routes use.

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

import re
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


def safe_netloc(parsed: ParseResult) -> str | None:
    """Authority of *parsed* with userinfo stripped, or ``None`` if unusable.

    ``ParseResult.port`` is computed on access, not at parse time, and it
    raises ``ValueError`` quoting the text it could not cast. For a truncated
    authority that text is the password::

        urlparse("https://alice:hunter2").port
        ValueError: Port could not be cast to integer value as 'hunter2'

    So a URL that parses cleanly can still leak on the attribute read, which is
    why reading ``.port`` directly is not safe either. The guard belongs at the
    point of use, not in :func:`parse_operator_url`: a ``mongodb://`` seed list
    (``h1:27017,h2:27017``) is a supported store URL whose port never casts.

    Args:
        parsed: A parsed URL.

    Returns:
        ``host`` or ``host:port``, with any ``user:pass@`` removed. IPv6 hosts
        are bracketed (``[::1]``), as a netloc or ``Host`` header needs them.
        ``None`` when the authority has no host or its port will not cast;
        callers decide whether that is a placeholder or a rejection. Nothing
        derived from the offending text is returned.
    """
    try:
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if not host:
        return None
    bracketed = f"[{host}]" if ":" in host else host
    return f"{bracketed}:{port}" if port is not None else bracketed


_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_USERINFO_RE = re.compile(r"://[^/\s]*@")
_QUERY_RE = re.compile(r"[?#]")


def redact_urls_in_text(text: str) -> str:
    """Strip userinfo and query/fragment from any URL inside *text*.

    Backend URLs reach a log line or a response through exception
    messages, and in this codebase they carry credentials in ``user:pass@``
    userinfo and tokens in the query string. Used by the health routes'
    ``full`` detail and by ``get_server_info``'s upstream error.

    The userinfo pattern is greedy up to the last ``@`` before the path,
    and every occurrence is replaced. A password may itself contain
    ``@``, and one whitespace-free token may carry more than one URL; a
    single non-greedy substitution leaked the tail of both.

    Known limit: the pattern stops at ``/``, so a password carrying a raw
    ``/`` is left unredacted. RFC 3986 requires that character to be
    percent-encoded in userinfo, and widening the pattern to cross ``/``
    would swallow everything between two URLs in one message. Stated
    rather than fixed.

    Deliberately textual rather than parsed. ``urlsplit(...).port`` raises
    ``ValueError`` on authorities this codebase actually supports — a
    ``mongodb://`` seed list such as ``h1:27017,h2:27017`` is the case
    that bit — and a redactor that raises emits the very message it was
    given to clean (see this module's docstring).

    ``_fetch`` redacts a URL it holds as a URL; this one has to find URLs
    inside free text, so the two are not the same function.

    Args:
        text: Free text, typically ``str(exc)``.

    Returns:
        *text* with every URL's userinfo, query and fragment removed.
    """

    def _strip(match: re.Match[str]) -> str:
        url = _USERINFO_RE.sub("://", match.group(0))
        return _QUERY_RE.split(url, maxsplit=1)[0]

    return _URL_RE.sub(_strip, text)
