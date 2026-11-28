"""Local HTTP user interface and NDJSON live research streaming endpoint."""

import argparse
import asyncio
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from uuid import uuid4

import httpx

from bootstrap import ROOT
from live_research import TOOLS, investigate
from research import connect, model_enabled, model_json, model_name
from skill_loader import discover_catalog

STATIC = {
    "/": ("index.html", "text/html"),
    "/research": ("research.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/intro.js": ("intro.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/intro.css": ("intro.css", "text/css"),
}


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, payload, mime, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path_only = self.path.split("?", 1)[0]
        if path_only == "/api/config":
            skills = list(discover_catalog().keys())
            config = {
                "llm": model_enabled(),
                "model": model_name() if model_enabled() else "",
                "sources": [
                    "OpenAlex, Crossref & arXiv fallback",
                    "Public HTML/PDF bodies",
                    "Hugging Face",
                    "Wikipedia",
                    "Saved passage evidence",
                ],
                "tools": sorted(TOOLS),
                "skills": skills,
                "chart_types": ["column"],
                "mcp_servers": ["research-discovery", "research-library"],
                "research_engine": "Task graph with isolated workers",
            }
            self.send_bytes(
                json.dumps(config, ensure_ascii=False).encode(), "application/json"
            )
        elif path_only in STATIC:
            filename, mime = STATIC[path_only]
            self.send_bytes((ROOT / "web" / filename).read_bytes(), mime)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path != "/api/research":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        allowed = {
            f"http://{self.headers.get('Host')}",
            f"https://{self.headers.get('Host')}",
        }
        if origin and origin not in allowed:
            self.send_bytes(
                b'{"message":"Open the studio at the same address as the research server."}',
                "application/json",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < size <= 8192
                or self.headers.get_content_type() != "application/json"
            ):
                raise ValueError
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError
            question = body.get("question", "")
            if not isinstance(question, str) or len(question) > 500:
                raise ValueError
            if not question.strip():
                self.send_bytes(
                    b'{"message":"Enter a research question."}',
                    "application/json",
                    HTTPStatus.BAD_REQUEST,
                )
                return
        except (ValueError, UnicodeError):
            self.send_bytes(
                b'{"message":"Invalid request."}',
                "application/json",
                HTTPStatus.BAD_REQUEST,
            )
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def emit(event, data):
            self.wfile.write(
                (
                    json.dumps({"event": event, **data}, ensure_ascii=False) + "\n"
                ).encode()
            )
            self.wfile.flush()

        try:
            if not model_enabled():
                raise ValueError(
                    "Configure a cloud model in .env to run live research."
                )

            async def run_live():
                failure = None
                directory = ROOT / "runs" / uuid4().hex
                async with connect(directory) as session:
                    try:
                        await investigate(
                            question.strip(), session, model_json, emit, directory
                        )
                    except (
                        ValueError,
                        HTTPError,
                        BrokenPipeError,
                        ConnectionResetError,
                    ) as exc:
                        failure = exc
                if failure is not None:
                    raise failure

            asyncio.run(run_live())
        except (BrokenPipeError, ConnectionResetError):
            return
        except ValueError as exc:
            emit("error", {"message": str(exc)})
        except HTTPError as exc:
            messages = {
                401: "The cloud model rejected its credentials. Check the API key.",
                403: "The cloud model denied access. Check the provider and model permissions.",
                429: "The cloud model is rate-limited or over its quota. Try again later.",
            }
            emit(
                "error",
                {
                    "message": messages.get(
                        exc.code, f"The cloud model request failed (HTTP {exc.code})."
                    )
                },
            )
        except httpx.HTTPError:
            emit(
                "error",
                {
                    "message": "Model connection failed. Check this project's .env and provider access."
                },
            )
        except Exception:
            logging.exception("Research query failed")
            try:
                emit(
                    "error",
                    {
                        "message": "The analysis service is unavailable. Check the server log."
                    },
                )
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Deep Research Studio.")
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        print(f"Deep Research Studio: http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
