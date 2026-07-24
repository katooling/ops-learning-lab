"""Loopback HTTP adapter for the Product Shell."""

from __future__ import annotations

import base64
from hashlib import sha256
import hmac
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

from .domain import SchemaError
from .promotion import PromotionService
from .promotion_models import PromotionError, PromotionPlan, StalePromotionError
from .shell_views import (
    _conflict_page,
    _decision_from_form,
    _index,
    _layout,
    _pack_detail,
    _pack_index,
    _preview_page,
    _readonly_detail,
    _review_detail,
)
from .staging import PackUpdateRepository
from .storage import StorageError


def make_server(
    repository: PackUpdateRepository,
    host: str,
    port: int,
    *,
    promotion: PromotionService | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Product Shell may bind only to the local loopback interface")
    server_key = secrets.token_bytes(32)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            status, body, content_type = self._route_get()
            self._send(status, body, content_type)

        def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            status, body, content_type = self._route_get()
            self._send(status, body, content_type, head_only=True)

        def _route_get(self) -> tuple[HTTPStatus, bytes, str]:
            if not self._trusted_host():
                return (
                    HTTPStatus.BAD_REQUEST,
                    b"invalid Host header\n",
                    "text/plain; charset=utf-8",
                )
            path = unquote(urlsplit(self.path).path)
            try:
                if path == "/":
                    return HTTPStatus.OK, _index(repository), "text/html; charset=utf-8"
                if path == "/health":
                    return HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8"
                if path == "/packs" and promotion is not None:
                    return (
                        HTTPStatus.OK,
                        _pack_index(promotion),
                        "text/html; charset=utf-8",
                    )
                if path.startswith("/packs/") and promotion is not None:
                    pack_id = path.removeprefix("/packs/")
                    if "/" not in pack_id:
                        pack = promotion.packs.get(pack_id)
                        if pack is not None:
                            return (
                                HTTPStatus.OK,
                                _pack_detail(pack),
                                "text/html; charset=utf-8",
                            )
                prefix = "/updates/"
                if path.startswith(prefix) and "/" not in path[len(prefix) :]:
                    update = repository.get(path[len(prefix) :])
                    accepted_packs = (
                        tuple(promotion.packs.list())
                        if promotion is not None
                        else ()
                    )
                    review_snapshot = {
                        "schema_version": 1,
                        "update_id": update.update_id,
                        "proposal_sha256": update.proposal_sha256,
                        "packs": {
                            pack.pack_id: {
                                "title": pack.title,
                                "version": pack.version,
                                "content_sha256": pack.content_sha256,
                            }
                            for pack in accepted_packs
                        },
                    }
                    signed_review, review_signature = self._signed_json(
                        review_snapshot
                    )
                    page = (
                        _review_detail(
                            update,
                            self._csrf(path),
                            signed_review,
                            review_signature,
                            accepted_packs,
                        )
                        if promotion is not None
                        else _readonly_detail(update)
                    )
                    return HTTPStatus.OK, page, "text/html; charset=utf-8"
            except StorageError:
                return self._not_found()
            except SchemaError:
                return (
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _layout("Unavailable", "<h1>Staged update is unavailable</h1>"),
                    "text/html; charset=utf-8",
                )
            return self._not_found()

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if promotion is None:
                self._method_not_allowed()
                return
            if not self._trusted_host():
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    b"invalid Host header\n",
                    "text/plain; charset=utf-8",
                )
                return
            path = unquote(urlsplit(self.path).path)
            if not self._trusted_origin():
                self._send(
                    HTTPStatus.FORBIDDEN,
                    b"invalid Origin header\n",
                    "text/plain; charset=utf-8",
                )
                return
            try:
                fields = self._form()
                update_id, action = self._promotion_route(path)
                if not hmac.compare_digest(
                    self._one(fields, "csrf-token"),
                    self._csrf(f"/updates/{update_id}"),
                ):
                    raise PromotionError("CSRF token is invalid")
                if action == "preview":
                    update = repository.get(update_id)
                    review_snapshot = self._verified_json(
                        fields,
                        "signed-review",
                        "review-signature",
                    )
                    self._validate_review_snapshot(review_snapshot, update)
                    decisions = tuple(
                        _decision_from_form(fields, index)
                        for index, _ in enumerate(update.proposed_claims)
                    )
                    target_id = self._one(fields, "target-pack-id").strip()
                    target_title = self._one(fields, "target-pack-title").strip()
                    base = review_snapshot["packs"].get(target_id)
                    if base is not None and base["title"] != target_title:
                        raise StalePromotionError(
                            "target Learning Pack title differs from the reviewed base"
                        )
                    plan = PromotionPlan(
                        update_id=update_id,
                        proposal_sha256=update.proposal_sha256,
                        target_pack_id=target_id,
                        target_pack_title=target_title,
                        expected_base_version=(
                            base["version"] if base is not None else None
                        ),
                        expected_base_sha256=(
                            base["content_sha256"] if base is not None else None
                        ),
                        decisions=decisions,
                    )
                    preview = promotion.preview(plan)
                    signed_plan, signature = self._signed_json(plan.to_dict())
                    self._send(
                        HTTPStatus.OK,
                        _preview_page(
                            update,
                            plan,
                            preview.changes,
                            preview.preview_sha256,
                            self._csrf(f"/updates/{update_id}"),
                            signed_plan,
                            signature,
                        ),
                    )
                    return

                if self._one(fields, "confirm") != "yes":
                    raise PromotionError("explicit Promotion confirmation is required")
                plan = promotion.plan_from_dict(
                    self._verified_json(
                        fields,
                        "signed-plan",
                        "plan-signature",
                    )
                )
                if plan.update_id != update_id:
                    raise PromotionError("Promotion route does not match the plan")
                result = promotion.commit(
                    plan,
                    self._one(fields, "preview-sha256"),
                )
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", f"/packs/{result.pack.pack_id}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
            except StalePromotionError as exc:
                plan_value = locals().get("plan")
                body = (
                    _conflict_page(plan_value, str(exc))
                    if isinstance(plan_value, PromotionPlan)
                    else _layout(
                        "Promotion conflict",
                        "<h1>Review is stale</h1><p>No changes were made. "
                        "Return to the staged update and preview again.</p>",
                    )
                )
                self._send(HTTPStatus.CONFLICT, body)
            except (
                ValueError,
                KeyError,
                json.JSONDecodeError,
                SchemaError,
                StorageError,
                PromotionError,
            ) as exc:
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    _layout(
                        "Review needs attention",
                        "<h1>Review needs attention</h1>"
                        f'<aside class="error">{escape(str(exc))}</aside>'
                        "<p>No changes were made.</p>",
                    ),
                )

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._method_not_allowed()

        do_PATCH = do_PUT
        do_DELETE = do_PUT

        def _promotion_route(self, path: str) -> tuple[str, str]:
            parts = path.strip("/").split("/")
            if (
                len(parts) != 3
                or parts[0] != "updates"
                or parts[2] not in {"preview", "promote"}
            ):
                raise PromotionError("unknown Promotion route")
            return parts[1], parts[2]

        def _form(self) -> dict[str, list[str]]:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != (
                "application/x-www-form-urlencoded"
            ):
                raise PromotionError("form content type is required")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise PromotionError("form content length is invalid") from exc
            if length < 0 or length > 65_536:
                raise PromotionError("review form is too large")
            encoded = self.rfile.read(length)
            return parse_qs(
                encoded.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=200,
            )

        @staticmethod
        def _one(fields: dict[str, list[str]], name: str) -> str:
            values = fields.get(name, [])
            if len(values) != 1:
                raise PromotionError(f"{name} must appear exactly once")
            return values[0]

        @staticmethod
        def _validate_review_snapshot(
            value: object,
            update,
        ) -> None:
            if not isinstance(value, dict) or set(value) != {
                "schema_version",
                "update_id",
                "proposal_sha256",
                "packs",
            }:
                raise PromotionError("review snapshot does not match the schema")
            if (
                value["schema_version"] != 1
                or value["update_id"] != update.update_id
                or value["proposal_sha256"] != update.proposal_sha256
                or not isinstance(value["packs"], dict)
            ):
                raise StalePromotionError("staged review snapshot is stale")
            for pack_id, base in value["packs"].items():
                if not isinstance(pack_id, str) or not isinstance(base, dict):
                    raise PromotionError("review pack base does not match the schema")
                if set(base) != {"title", "version", "content_sha256"}:
                    raise PromotionError("review pack base does not match the schema")
                if (
                    not isinstance(base["title"], str)
                    or not isinstance(base["version"], int)
                    or isinstance(base["version"], bool)
                    or base["version"] < 1
                    or not isinstance(base["content_sha256"], str)
                    or len(base["content_sha256"]) != 64
                ):
                    raise PromotionError("review pack base does not match the schema")

        @staticmethod
        def _signed_json(value: object) -> tuple[str, str]:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            signed = base64.urlsafe_b64encode(encoded).decode("ascii")
            signature = hmac.new(server_key, encoded, sha256).hexdigest()
            return signed, signature

        @staticmethod
        def _verified_json(
            fields: dict[str, list[str]],
            value_name: str,
            signature_name: str,
        ) -> object:
            encoded = base64.b64decode(
                Handler._one(fields, value_name).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            expected = hmac.new(server_key, encoded, sha256).hexdigest()
            if not hmac.compare_digest(
                Handler._one(fields, signature_name),
                expected,
            ):
                raise PromotionError("signed review integrity check failed")
            return json.loads(encoded)

        def _method_not_allowed(self) -> None:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            allowed = "GET, HEAD, POST" if promotion is not None else "GET, HEAD"
            self.send_header("Allow", allowed)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _trusted_host(self) -> bool:
            value = self.headers.get("Host", "")
            try:
                parsed = urlsplit(f"//{value}")
                return (
                    parsed.hostname in {"127.0.0.1", "localhost"}
                    and parsed.port == self.server.server_address[1]
                )
            except ValueError:
                return False

        def _trusted_origin(self) -> bool:
            return self.headers.get("Origin", "") == (
                f"http://{self.headers.get('Host', '')}"
            )

        def _csrf(self, path: str) -> str:
            return hmac.new(server_key, path.encode("utf-8"), sha256).hexdigest()

        @staticmethod
        def _not_found() -> tuple[HTTPStatus, bytes, str]:
            return (
                HTTPStatus.NOT_FOUND,
                _layout("Not found", "<h1>Not found</h1>"),
                "text/html; charset=utf-8",
            )

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str = "text/html; charset=utf-8",
            *,
            head_only: bool = False,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
