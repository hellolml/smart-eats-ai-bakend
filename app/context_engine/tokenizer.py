from __future__ import annotations

from typing import Any


class ApproxTokenCounter:
    def count_text(self, text: str | None) -> int:
        if not text:
            return 0
        ascii_count = sum(1 for ch in text if ord(ch) < 128)
        non_ascii_count = len(text) - ascii_count
        return max(1, int(ascii_count / 4) + non_ascii_count)

    def count_event(self, event: Any) -> int:
        content = getattr(event, "content", None)
        return self.count_text(content) + 4

    def count_block(self, block: Any) -> int:
        return self.count_text(getattr(block, "content", None)) + 4

    def count_messages(self, messages: list[Any]) -> int:
        total = 0
        for message in messages:
            total += self.count_text(str(getattr(message, "content", "") or "")) + 4
        return total
