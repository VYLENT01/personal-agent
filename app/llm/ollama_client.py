"""کلاینت HTTP رسمی Ollama — تنها جایی که جزئیات Ollama دیده می‌شود.

از HTTP API رسمی Ollama استفاده می‌کند (بدون وابستگی به کتابخانه خاص):
- GET  /api/version  → نسخه سرویس
- GET  /api/tags     → مدل‌های نصب‌شده
- POST /api/chat     → گفت‌وگو (استریم NDJSON یا کامل)

همه خطاها به خطاهای استاندارد app.llm.base نگاشت می‌شوند تا
هسته ایجنت هیچ چیزی درباره نوع بک‌اند نداند.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Iterator, Mapping, Optional, Sequence

import requests

from app.llm.base import (
    BaseLLMClient,
    CancelToken,
    ChatMessage,
    LLMConnectionError,
    LLMError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
    ModelInfo,
    StreamChunk,
)
from app.llm.response_parser import extract_content

logger = logging.getLogger(__name__)

_STATS_KEYS = ("eval_count", "prompt_eval_count", "total_duration", "eval_duration", "load_duration")


class OllamaClient(BaseLLMClient):
    """پیاده‌سازی قرارداد LLM روی سرویس Ollama."""

    def __init__(
        self,
        base_url: str,
        connect_timeout: float = 5.0,
        read_timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._active_streams: set[requests.Response] = set()

    # ------------------------------------------------------------------
    # زیرساخت مشترک درخواست‌ها
    # ------------------------------------------------------------------

    def _connection_message(self, exc: Optional[Exception] = None) -> str:
        detail = f" جزئیات فنی: {exc}" if exc else ""
        return (
            "اتصال به Ollama برقرار نشد "
            f"({self.base_url}). مطمئن شوید Ollama در حال اجراست ( دستور: ollama serve ).{detail}"
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        timeout: Optional[tuple[float, float]] = None,
        stream: bool = False,
    ) -> requests.Response:
        """ارسال درخواست با نگاشت متمرکز خطاها؛ فراخوان باید پاسخ را ببندد."""
        url = f"{self.base_url}{path}"
        effective_timeout = timeout or (self.connect_timeout, self.read_timeout)
        try:
            response = self._session.request(
                method, url, json=json_body, timeout=effective_timeout, stream=stream
            )
        except requests.exceptions.ConnectionError as exc:
            raise LLMConnectionError(self._connection_message(exc)) from exc
        except requests.exceptions.Timeout as exc:
            raise LLMTimeoutError(
                f"زمان انتظار برای پاسخ Ollama به پایان رسید "
                f"(connect={self.connect_timeout:.0f}s / read={self.read_timeout:.0f}s)."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"خطای شبکه در ارتباط با Ollama: {exc}") from exc
        self._check_status(response)
        return response

    def _check_status(self, response: requests.Response) -> None:
        if response.status_code < 400:
            return
        error_text = ""
        try:
            body = response.json()
            error_text = str(body.get("error") or "")
        except ValueError:
            error_text = response.text[:200] if response.text else ""
        message = f"Ollama خطای HTTP {response.status_code} برگرداند"
        if error_text:
            message += f": {error_text}"
        raise LLMResponseError(message)

    # ------------------------------------------------------------------
    # قرارداد BaseLLMClient
    # ------------------------------------------------------------------

    def is_available(self) -> tuple[bool, str]:
        """بررسی سلامت سرویس؛ هرگز exception نمی‌دهد تا در UI قابل استفاده باشد."""
        try:
            response = self._send("GET", "/api/version", timeout=(2.0, 4.0))
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMResponseError("پاسخ /api/version معتبر نبود.") from exc
            response.close()
            return True, str(data.get("version", "نامشخص"))
        except LLMError as exc:
            return False, str(exc)

    def list_models(self) -> list[ModelInfo]:
        response = self._send("GET", "/api/tags")
        with response:
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMResponseError("پاسخ /api/tags معتبر نبود.") from exc
        models: list[ModelInfo] = []
        for item in data.get("models", []) or []:
            name = item.get("name") or item.get("model")
            if not name:
                continue
            models.append(
                ModelInfo(
                    name=str(name),
                    size_bytes=item.get("size"),
                    modified_at=item.get("modified_at"),
                    digest=item.get("digest"),
                    details=item.get("details") or {},
                )
            )
        models.sort(key=lambda m: m.name.lower())
        return models

    def chat(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> LLMResponse:
        if cancel is not None and cancel.is_cancelled():
            raise LLMError("درخواست پیش از ارسال لغو شد.")
        payload = self._payload(messages, model, options)
        payload["stream"] = False
        response = self._send("POST", "/api/chat", json_body=payload)
        with response:
            try:
                data = response.json()
            except ValueError as exc:
                raise LLMResponseError("پاسخ /api/chat معتبر نبود (JSON خراب).") from exc
        if data.get("error"):
            raise LLMResponseError(f"خطای Ollama: {data['error']}")
        message = data.get("message") or {}
        raw_content = message.get("content") or ""
        content, thinking = extract_content(raw_content)
        return LLMResponse(
            content=content,
            thinking=thinking,
            model=data.get("model") or model,
            done_reason=data.get("done_reason"),
            prompt_eval_count=data.get("prompt_eval_count"),
            eval_count=data.get("eval_count"),
            total_duration_ns=data.get("total_duration"),
            meta={"raw_content": raw_content},
        )

    def chat_stream(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str] = None,
        *,
        options: Optional[Mapping[str, Any]] = None,
        cancel: Optional[CancelToken] = None,
    ) -> Iterator[StreamChunk]:
        payload = self._payload(messages, model, options)
        payload["stream"] = True
        response = self._send("POST", "/api/chat", json_body=payload, stream=True)
        self._register(response)
        try:
            yield from self._read_stream(response, cancel)
        finally:
            self._unregister(response)
            response.close()

    def cancel_active(self) -> int:
        """بستن جریان‌های در-حال-اجرا (فراخوانی از thread رابط کاربری مجاز است).

        برای لغو فوری، علاوه بر response.close() خودِ socket زیرین را shutdown
        می‌کنیم تا recv بلاک‌شده بی‌درنگ با خطا برگردد و thread کارگری آزاد شود.
        """
        import socket as socket_mod

        closed = 0
        with self._lock:
            streams = list(self._active_streams)
        for response in streams:
            try:
                try:
                    file_wrapper = getattr(response.raw, "_fp", None)
                    buffered = getattr(file_wrapper, "fp", None) if file_wrapper else None
                    fd = buffered.fileno() if buffered is not None else None
                    if fd is not None:
                        sock = socket_mod.socket(fileno=fd)
                        try:
                            sock.shutdown(socket_mod.SHUT_RDWR)
                        finally:
                            sock.detach()
                except Exception:  # noqa: BLE001 - ساختار داخلی ممکن است متفاوت باشد
                    pass
                response.close()
                closed += 1
            except Exception:  # noqa: BLE001 - بستن socket از thread دیگر ممکن است خطای بی‌ضرر دهد
                logger.debug("بستن جریان فعال با خطا مواجه شد", exc_info=True)
        return closed

    def close(self) -> None:
        try:
            self.cancel_active()
        finally:
            self._session.close()

    # ------------------------------------------------------------------
    # داخلی
    # ------------------------------------------------------------------

    def _payload(
        self,
        messages: Sequence[ChatMessage],
        model: Optional[str],
        options: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [message.to_api() for message in messages],
            "options": dict(options or {}),
        }

    def _read_stream(
        self, response: requests.Response, cancel: Optional[CancelToken]
    ) -> Iterator[StreamChunk]:
        try:
            for raw_line in response.iter_lines(chunk_size=1):
                if cancel is not None and cancel.is_cancelled():
                    raise LLMError("__CANCELLED__")
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LLMResponseError("پاسخ نامعتبر از Ollama دریافت شد (NDJSON خراب).") from exc
                if obj.get("error"):
                    raise LLMResponseError(f"خطای Ollama: {obj['error']}")
                message = obj.get("message") or {}
                chunk = StreamChunk(
                    content=message.get("content") or "",
                    done=bool(obj.get("done", False)),
                    done_reason=obj.get("done_reason"),
                    meta={key: obj[key] for key in _STATS_KEYS if key in obj},
                )
                yield chunk
                if chunk.done:
                    return
        except LLMError as exc:
            if str(exc) == "__CANCELLED__" or (cancel is not None and cancel.is_cancelled()):
                # لغو خودخواسته: بسته شدن socket توسط cancel_active هم اینجا می‌رسد
                from app.llm.base import LLMCancelledError

                raise LLMCancelledError("درخواست به‌درخواست کاربر لغو شد.") from exc
            raise
        except requests.exceptions.Timeout as exc:
            raise LLMTimeoutError(
                f"زمان انتظار بین قطعات پاسخ مدل ({self.read_timeout:.0f} ثانیه) به پایان رسید."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            if cancel is not None and cancel.is_cancelled():
                from app.llm.base import LLMCancelledError

                raise LLMCancelledError("درخواست به‌درخواست کاربر لغو شد.") from exc
            raise LLMConnectionError(self._connection_message(exc)) from exc
        except requests.exceptions.RequestException as exc:
            # مانند ChunkedEncodingError پس از بستن socket در حین لغو
            if cancel is not None and cancel.is_cancelled():
                from app.llm.base import LLMCancelledError

                raise LLMCancelledError("درخواست به‌درخواست کاربر لغو شد.") from exc
            raise LLMConnectionError(
                f"ارتباط با Ollama حین دریافت پاسخ قطع شد: {exc}"
            ) from exc
        except (AttributeError, ValueError, OSError) as exc:
            # بستن socket زیرین حین لغو می‌تواند exceptionهای داخلی کتابخانه
            # استاندارد تولید کند (مثل fp=None در http.client)؛ در حالت لغو
            # بی‌ضرر است و به LLMCancelledError نگاشت می‌شود.
            if cancel is not None and cancel.is_cancelled():
                from app.llm.base import LLMCancelledError

                raise LLMCancelledError("درخواست به‌درخواست کاربر لغو شد.") from exc
            raise LLMConnectionError(
                f"ارتباط با Ollama حین دریافت پاسخ قطع شد: {exc}"
            ) from exc

    def _register(self, response: requests.Response) -> None:
        with self._lock:
            self._active_streams.add(response)

    def _unregister(self, response: requests.Response) -> None:
        with self._lock:
            self._active_streams.discard(response)
