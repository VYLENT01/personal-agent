"""صفحه گفت‌وگو: نمایش پیام‌ها، پاسخ استریم، تفکر مدل، ابزارها و لغو.

- پیام‌ها به‌صورت حباب‌های کاربر/دستیار/خطا نمایش داده می‌شوند.
- پاسخ مدل قطعه‌قطعه (ContentDelta) و تفکر مدل جداگانه (ThinkingDelta) می‌رسد.
- فراخوانی ابزار (فاز ۲) در حباب جدا: نام، وضعیت، پارامترها، نتیجه/خطا.
- نوار وضعیت تعداد مراحل اجرای Agent را نشان می‌دهد.
- دکمه ارسال حین اجرا به «لغو» تبدیل می‌شود؛ UI در تمام مدت پاسخ‌گو می‌ماند.
"""
from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.agent.orchestrator import (
    AgentEvent,
    ChatOrchestrator,
    ContentDelta,
    ThinkingDelta,
    ToolCallFinished,
    ToolCallRequested,
    ToolCallStarted,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
)
from app.bootstrap import AppContext

logger = logging.getLogger(__name__)


class ChatWorker(QThread):
    """اجرای نوبت گفت‌وگو در thread جدا؛ رویدادها به صفحه می‌رسند."""

    event = Signal(object)

    def __init__(self, orchestrator: ChatOrchestrator, text: str, parent: object = None) -> None:
        super().__init__(parent)
        self._orchestrator = orchestrator
        self._text = text

    def run(self) -> None:
        try:
            for agent_event in self._orchestrator.stream_turn(self._text):
                self.event.emit(agent_event)
        except Exception as exc:  # noqa: BLE001 - هیچ خطایی نباید thread را بی‌صدا بکشد
            logger.exception("خطای غیرمنتظره در worker گفت‌وگو")
            self.event.emit(
                TurnFailed(error=f"خطای داخلی: {exc}", kind="InternalError", recoverable=False)
            )


class MessageBubble(QFrame):
    """حباب یک پیام: کاربر / دستیار / خطا."""

    def __init__(self, role: str, rtl: bool, parent: object | None = None) -> None:
        super().__init__(parent)
        self._role = role
        self.setObjectName(
            {"user": "bubbleUser", "assistant": "bubbleAssistant", "error": "bubbleError"}.get(
                role, "bubbleAssistant"
            )
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        if role == "assistant":
            self.thinking_label = QLabel("")
            self.thinking_label.setObjectName("thinkingText")
            self.thinking_label.setWordWrap(True)
            self.thinking_label.setVisible(False)
            align = Qt.AlignRight if rtl else Qt.AlignLeft
            self.thinking_label.setAlignment(align)
            layout.addWidget(self.thinking_label)

        self.content_label = QLabel("")
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.content_label.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        layout.addWidget(self.content_label)

        if role == "assistant":
            self.meta_label = QLabel("")
            self.meta_label.setObjectName("bubbleMeta")
            layout.addWidget(self.meta_label)

    def append_content(self, text: str) -> None:
        self.content_label.setText(self.content_label.text() + text)

    def append_thinking(self, text: str) -> None:
        self.thinking_label.setVisible(True)
        self.thinking_label.setText(self.thinking_label.text() + text)

    def finalize(self, content: str, thinking: str = "", meta: str = "") -> None:
        """جایگزینی متن نهایی (مرجع) و متادیتا."""
        self.content_label.setText(content)
        if thinking:
            self.thinking_label.setVisible(True)
            self.thinking_label.setText(thinking)
        self.meta_label.setText(meta)

    def mark_cancelled(self, partial: str) -> None:
        if partial:
            self.content_label.setText(partial)
        self.meta_label.setText("— پاسخ به‌دستور شما لغو شد —")


class ToolBubble(QFrame):
    """حباب فراخوانی ابزار (فاز ۲): نام، وضعیت، پارامترها، نتیجه/خطا."""

    def __init__(self, name: str, args: dict, rtl: bool, parent: object | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bubbleTool")
        self._rtl = rtl
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # سربرگ: آیکن + نام ابزار + وضعیت
        self.tool_name_label = QLabel(f"🔧 {name}")
        self.tool_name_label.setObjectName("toolName")
        self.tool_name_label.setWordWrap(True)
        self.tool_name_label.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        layout.addWidget(self.tool_name_label)

        # پارامترهای فراخوانی
        args_text = json.dumps(args, ensure_ascii=False) if args else "{}"
        self.args_label = QLabel(f"پارامترها: {args_text}")
        self.args_label.setObjectName("toolArgs")
        self.args_label.setWordWrap(True)
        self.args_label.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        layout.addWidget(self.args_label)

        # نتیجه (در پایان پر می‌شود)
        self.result_label = QLabel("")
        self.result_label.setObjectName("toolResult")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.result_label.setAlignment(Qt.AlignRight if rtl else Qt.AlignLeft)
        layout.addWidget(self.result_label)

        self._set_status("در حال اجرا…")

    def _set_status(self, text: str) -> None:
        self.setToolTip(text)

    def mark_running(self) -> None:
        self._set_status("در حال اجرا…")

    def mark_finished(self, result) -> None:
        """نمایش نتیجه ساختاریافته ToolResult (موفق یا خطا)."""
        from app.tools.base import ToolResult

        assert isinstance(result, ToolResult)
        if result.success:
            preview = _preview_data(result.data)
            self.result_label.setText(f"✅ نتیجه: {preview}")
            self._set_status(f"موفق — {result.duration:.2f} ثانیه")
        else:
            self.result_label.setText(f"⛔ خطای ابزار: {result.error}")
            self._set_status("ناموفق")


def _preview_data(data, limit: int = 300) -> str:
    """پیش‌نمایش متنی داده نتیجه ابزار برای حباب UI."""
    if data is None:
        return "—"
    if isinstance(data, str):
        text = data
    else:
        try:
            text = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(data)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


class ChatPage(QWidget):
    """صفحه اصلی گفت‌وگو."""

    def __init__(self, context: AppContext, parent: object | None = None) -> None:
        super().__init__(parent)
        rtl = context.settings.language == "fa"
        self._context = context
        self._worker: ChatWorker | None = None
        self._bubbles: list[MessageBubble | ToolBubble] = []
        self._tool_bubbles: list[ToolBubble] = []
        self._current_assistant: MessageBubble | None = None
        self._agent_steps = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # --- نوار بالای صفحه ---
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("گفت‌وگو" if rtl else "Chat")
        title.setObjectName("pageHeader")
        header_layout.addWidget(title)
        root.addWidget(header)

        # --- ناحیه پیام‌ها ---
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setObjectName("chatScroll")
        container = QWidget()
        self.messages_layout = QVBoxLayout(container)
        self.messages_layout.setContentsMargins(4, 4, 4, 4)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(container)
        root.addWidget(self.scroll, 1)

        self.empty_hint = QLabel("برای شروع، پیام خود را بنویسید." if rtl else "Write a message to start.")
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.messages_layout.insertWidget(0, self.empty_hint)

        # --- ناحیه ورودی ---
        input_row = QVBoxLayout()
        input_row.setSpacing(6)
        self.input_edit = _InputEdit(self)
        self.input_edit.setPlaceholderText(
            "پیام خود را بنویسید… (Enter = ارسال، Shift+Enter = خط جدید)" if rtl
            else "Type your message… (Enter = send, Shift+Enter = new line)"
        )
        self.input_edit.setMaximumHeight(120)
        input_row.addWidget(self.input_edit)

        button_row = QVBoxLayout()
        self.send_button = QPushButton("ارسال" if rtl else "Send")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self._on_send_clicked)
        self.send_button.setDefault(True)
        button_row.addWidget(self.send_button)

        self.status_label = QLabel("آماده" if rtl else "Ready")
        self.status_label.setObjectName("chatStatus")
        input_row.addWidget(self.status_label)

        root.addLayout(input_row)

    # ------------------------------------------------------------------
    # ارسال / لغو
    # ------------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._on_cancel_clicked()
            return
        self.submit()

    def submit(self, text: str | None = None) -> None:
        """ارسال پیام کاربر و شروع نوبت پاسخ‌گویی در thread جدا."""
        content = (text if text is not None else self.input_edit.toPlainText()).strip()
        if not content or (self._worker is not None and self._worker.isRunning()):
            return
        self.input_edit.clear()
        self._hide_empty_hint()
        self._add_bubble("user", content)
        self._set_busy(True)
        self._current_assistant = None
        self._tool_bubbles.clear()
        self._agent_steps = 0
        self._worker = ChatWorker(self._context.orchestrator, content)
        self._worker.event.connect(self._on_agent_event)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_cancel_clicked(self) -> None:
        self._context.orchestrator.request_cancel()
        self.status_label.setText("در حال لغو…" if self._context.settings.language == "fa" else "Cancelling…")

    # ------------------------------------------------------------------
    # رویدادهای ایجنت
    # ------------------------------------------------------------------

    def _on_agent_event(self, agent_event: AgentEvent) -> None:
        if isinstance(agent_event, TurnStarted):
            bubble = self._add_bubble("assistant", "")
            self._current_assistant = bubble
            mode_text = {
                "native": " (ابزار بومی)",
                "fallback": " (ابزار با پروتکل JSON)",
                "none": "",
            }.get(agent_event.tool_mode, "")
            self.status_label.setText(f"در حال تولید پاسخ…{mode_text}")
            self._agent_steps = 0
            self._scroll_to_bottom()

        elif isinstance(agent_event, ThinkingDelta):
            if self._current_assistant is not None:
                self._current_assistant.append_thinking(agent_event.text)
                self._scroll_to_bottom()

        elif isinstance(agent_event, ContentDelta):
            if self._current_assistant is not None:
                self._current_assistant.append_content(agent_event.text)
                self._scroll_to_bottom()

        elif isinstance(agent_event, ToolCallRequested):
            # نمایش نام ابزار انتخاب‌شده + پارامترها (وضعیت: درخواست‌شده)
            self._agent_steps += 1
            call = agent_event.call
            bubble = self._add_tool_bubble(call.name, call.args)
            self.status_label.setText(
                f"مرحله {self._agent_steps}: ابزار {call.name} درخواست شد"
            )

        elif isinstance(agent_event, ToolCallStarted):
            bubble = self._last_tool_bubble()
            if bubble is not None:
                bubble.mark_running()
            self.status_label.setText(
                f"مرحله {self._agent_steps}: در حال اجرای {agent_event.name}…"
            )

        elif isinstance(agent_event, ToolCallFinished):
            self._agent_steps += 1
            bubble = self._last_tool_bubble()
            if bubble is not None:
                bubble.mark_finished(agent_event.result)
            status = "انجام شد" if agent_event.result.success else "خطای ابزار"
            self.status_label.setText(
                f"مرحله {self._agent_steps}: {agent_event.result.tool} → {status}"
            )

        elif isinstance(agent_event, TurnCompleted):
            model = agent_event.model or ""
            parts = []
            if model:
                parts.append(f"مدل: {model}")
            parts.append(f"مراحل: {agent_event.iterations}")
            if agent_event.tool_calls:
                parts.append(f"فراخوانی ابزار: {agent_event.tool_calls}")
            parts.append(f"{agent_event.duration:.1f} ثانیه")
            if self._current_assistant is not None:
                self._current_assistant.finalize(
                    agent_event.content, agent_event.thinking, " — ".join(parts)
                )
            self.status_label.setText("انجام شد")
            self._set_busy(False)

        elif isinstance(agent_event, TurnCancelled):
            if self._current_assistant is not None:
                self._current_assistant.mark_cancelled(agent_event.partial)
            self.status_label.setText("لغو شد")
            self._set_busy(False)

        elif isinstance(agent_event, TurnFailed):
            error_text = agent_event.error
            self._add_bubble("error", error_text)
            self.status_label.setText("خطا")
            self._set_busy(False)

    # ------------------------------------------------------------------
    # ابزارهای داخلی
    # ------------------------------------------------------------------

    def _add_tool_bubble(self, name: str, args: dict) -> ToolBubble:
        bubble = ToolBubble(name, args, rtl=self._context.settings.language == "fa")
        index = self.messages_layout.count() - 1  # قبل از stretch انتهایی
        self.messages_layout.insertWidget(index, bubble)
        self._bubbles.append(bubble)
        self._tool_bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def _last_tool_bubble(self) -> ToolBubble | None:
        return self._tool_bubbles[-1] if self._tool_bubbles else None

    def _add_bubble(self, role: str, content: str) -> MessageBubble:
        bubble = MessageBubble(role, rtl=self._context.settings.language == "fa")
        if content:
            bubble.append_content(content)
        index = self.messages_layout.count() - 1  # قبل از stretch انتهایی
        self.messages_layout.insertWidget(index, bubble)
        self._bubbles.append(bubble)
        self._scroll_to_bottom()
        return bubble

    def _hide_empty_hint(self) -> None:
        self.empty_hint.hide()

    def _set_busy(self, busy: bool) -> None:
        rtl = self._context.settings.language == "fa"
        if busy:
            self.send_button.setText("لغو" if rtl else "Cancel")
        else:
            self.send_button.setText("ارسال" if rtl else "Send")
            self._worker = None
            self._current_assistant = None

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 40
        if at_bottom:
            bar.setValue(bar.maximum())

    def new_conversation(self) -> None:
        """شروع گفت‌وگوی تازه."""
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText("ابتدا عملیات جاری را لغو کنید.")
            return
        self._context.orchestrator.new_conversation()
        for bubble in self._bubbles:
            bubble.setParent(None)
            bubble.deleteLater()
        self._bubbles.clear()
        self._tool_bubbles.clear()
        self._current_assistant = None
        self._agent_steps = 0
        self.empty_hint.show()
        self.status_label.setText("آماده")


class _InputEdit(QPlainTextEdit):
    """جعبه ورودی با ارسال مستقیم با Enter."""

    def __init__(self, page: ChatPage) -> None:
        super().__init__()
        self._page = page

    def keyPressEvent(self, event) -> None:  # noqa: N802 - نام Qt
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._page.submit()
            return
        super().keyPressEvent(event)
