"""سرور HTTP ساختگی Ollama برای تست پروتکل بدون Ollama واقعی.

 NDJSON استریم، خطا، تأخیر و قطع اتصال را شبیه‌سازی می‌کند تا
مسیرهای واقعی (Timeout / ConnectionError / Cancel) آزمایش شوند.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


class MockOllamaServer:
    """سرور ساختگی با حالت‌های قابل تنظیم."""

    def __init__(
        self,
        *,
        models: Optional[list[dict[str, Any]]] = None,
        version: str = "0.12.0",
        stream_script: Optional[list[tuple[str, float]]] = None,
        fail_mode: Optional[str] = None,
    ) -> None:
        self.models = models or [
            {"name": "qwen3:8b", "size": 5_200_000_000, "details": {"parameter_size": "8.2B"}},
            {"name": "gemma3:4b", "size": 3_300_000_000, "details": {"parameter_size": "4.3B"}},
        ]
        self.version = version
        # اسکریپت استریم: لیست (متن قطعه، تأخیر پیش از ارسال ثانیه)
        self.stream_script = stream_script or [("سلام", 0.0), ("! در خدمتم.", 0.0)]
        self.fail_mode = fail_mode  # None | "drop" | "slow" | "error_json" | "bad_ndjson"
        self.received: list[dict[str, Any]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _make_handler(server: MockOllamaServer) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # noqa: N802
            pass  # خروجی تست تمیز بماند

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/version":
                if server.fail_mode == "drop":
                    self.close_connection = True
                    return
                self._send_json({"version": server.version})
            elif self.path == "/api/tags":
                self._send_json({"models": server.models})
            else:
                self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._send_json({"error": "invalid json"}, status=400)
                return
            server.received.append(body)

            if self.path != "/api/chat":
                self._send_json({"error": "not found"}, status=404)
                return

            if server.fail_mode == "error_json":
                self._send_json({"error": f"model '{body.get('model')}' not found, try pulling it first"}, status=404)
                return
            if server.fail_mode == "bad_ndjson":
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                self.wfile.write("این خط JSON نیست".encode("utf-8"))
                self.wfile.flush()
                return
            if server.fail_mode == "slow":
                time.sleep(1.5)
                self._send_json({"message": {"role": "assistant", "content": "دیر"}, "done": True})
                return

            # حالت عادی: استریم NDJSON
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for text, delay in server.stream_script:
                if delay:
                    time.sleep(delay)
                line = {
                    "model": body.get("model", "fake"),
                    "message": {"role": "assistant", "content": text},
                    "done": False,
                }
                self.wfile.write((json.dumps(line) + "\n").encode("utf-8"))
                self.wfile.flush()
            final = {
                "model": body.get("model", "fake"),
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "eval_count": 12,
                "prompt_eval_count": 30,
                "total_duration": 123456789,
            }
            self.wfile.write((json.dumps(final) + "\n").encode("utf-8"))
            self.wfile.flush()

    return Handler
