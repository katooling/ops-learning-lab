"""Minimal semantic HTTP Product Shell for reviewing staged Pack Updates."""

from __future__ import annotations

from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .domain import PackMatch, SchemaError, StagedPackUpdate
from .staging import PackUpdateRepository
from .storage import StorageError


STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }
body { max-width: 58rem; margin: 0 auto; padding: 1.5rem; }
a { color: LinkText; }
.status { border: 1px solid; border-radius: .3rem; padding: .25rem .5rem; }
article, section { margin-block: 1.5rem; }
.trust { border-inline-start: .35rem solid; padding: .75rem 1rem; }
dt { font-weight: 700; }
dd { margin-bottom: .75rem; }
@media (max-width: 20rem) { body { padding: .75rem; } code { overflow-wrap: anywhere; } }
"""


def _layout(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Ops Learning Lab</title>
<style>{STYLE}</style>
</head>
<body>
<header><a href="/">Ops Learning Lab</a></header>
<main>{body}</main>
</body>
</html>
""".encode("utf-8")


def _match_summary(match: PackMatch) -> str:
    if match.kind == "strong":
        candidate = match.candidates[0]
        return f"Proposed destination: {candidate.title}"
    if match.kind == "ambiguous":
        return "Learner choice required"
    return "Proposed new Learning Pack"


def _index(repository: PackUpdateRepository) -> bytes:
    updates = list(repository.list())
    items = "".join(
        f'<li><a href="/updates/{escape(update.update_id)}">'
        f"{escape(update.update_id)}</a> — {escape(_match_summary(update.match))}</li>"
        for update in updates
    )
    if not items:
        items = "<li>No staged updates yet.</li>"
    return _layout(
        "Staged updates",
        "<h1>Staged Pack Updates</h1>"
        "<p>Review proposals here. Capture Mode never changes accepted packs "
        "or starts a lesson.</p>"
        f"<ul>{items}</ul>",
    )


def _detail(update: StagedPackUpdate) -> bytes:
    candidate_items = "".join(
        "<li>"
        f"{escape(candidate.title)} — matched "
        f"{escape(', '.join(candidate.matched_terms))}"
        "</li>"
        for candidate in update.match.candidates
    )
    if not candidate_items:
        candidate_items = "<li>No accepted pack candidate.</li>"
    reasons = "".join(
        f"<li>{escape(reason)}</li>" for reason in update.match.reasons
    )
    claims = "".join(
        "<article>"
        f"<h3>{escape(claim.fact_status.title())} candidate</h3>"
        f"<p>{escape(claim.text)}</p>"
        "<dl>"
        f"<dt>Proposal ID</dt><dd><code>{escape(claim.proposal_id)}</code></dd>"
        f"<dt>Source intake</dt><dd><code>{escape(claim.source_intake_id)}</code></dd>"
        f"<dt>Source digest</dt><dd><code>{escape(claim.source_content_sha256)}</code></dd>"
        "</dl>"
        "</article>"
        for claim in update.proposed_claims
    )
    if not claims:
        claims = (
            "<p>No explicit claims were found. Add lines such as "
            "<code>Claim [unverified]: A concise candidate.</code> and capture again.</p>"
        )
    redactions = "".join(
        f"<li>{escape(redaction.replace(':', ': '))}</li>"
        for redaction in update.redactions
    )
    return _layout(
        update.update_id,
        f"<h1>Staged Pack Update</h1>"
        '<aside class="trust"><strong>Private staged proposal.</strong> '
        "Raw intake is not available to this page.</aside>"
        '<p><span class="status">staged</span></p>'
        f"<p><strong>{escape(_match_summary(update.match))}</strong></p>"
        "<section><h2>Why this match?</h2>"
        f"<ul>{reasons}</ul><h3>Candidates</h3><ul>{candidate_items}</ul></section>"
        f"<section><h2>Proposed claims</h2>{claims}</section>"
        f"<section><h2>Privacy redactions</h2><ul>{redactions}</ul></section>"
        "<section><h2>Capture result</h2>"
        "<p>Nothing has changed in the accepted pack. No lesson has started.</p>"
        "<dl>"
        f"<dt>Source type</dt><dd>{escape(update.source.source_type)}</dd>"
        f"<dt>Observed at</dt><dd>{escape(update.source.observed_at)}</dd>"
        f"<dt>Immutable proposal digest</dt><dd><code>{escape(update.proposal_sha256)}</code></dd>"
        "</dl></section>",
    )


def make_server(
    repository: PackUpdateRepository,
    host: str,
    port: int,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("Product Shell may bind only to the local loopback interface")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._trusted_host():
                self._send(HTTPStatus.BAD_REQUEST, b"invalid Host header\n", "text/plain; charset=utf-8")
                return
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/":
                    self._send(HTTPStatus.OK, _index(repository))
                    return
                if path == "/health":
                    self._send(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
                    return
                prefix = "/updates/"
                if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                    self._send(
                        HTTPStatus.OK,
                        _detail(repository.get(path[len(prefix) :])),
                    )
                    return
            except StorageError:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    _layout("Not found", "<h1>Not found</h1>"),
                )
                return
            except SchemaError:
                self._send(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _layout("Unavailable", "<h1>Staged update is unavailable</h1>"),
                )
                return
            self._send(
                HTTPStatus.NOT_FOUND,
                _layout("Not found", "<h1>Not found</h1>"),
            )

        def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if not self._trusted_host():
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    b"",
                    "text/plain; charset=utf-8",
                )
                return
            self._send(HTTPStatus.OK, b"")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._method_not_allowed()

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _trusted_host(self) -> bool:
            value = self.headers.get("Host", "")
            hostname = value.rsplit(":", 1)[0].strip("[]").lower()
            return hostname in {"127.0.0.1", "::1", "localhost"}

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str = "text/html; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
