from __future__ import annotations

import asyncio
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext


class _ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "tr"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        self.text_parts.append(text)


def _collapse_text(value: str, *, max_chars: int = 20000) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars].rstrip()


def _fetch_url_sync(url: str, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 SmartEatsTravelPlanner/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(2_000_000)
        content_type = response.headers.get("content-type", "")
    encoding = "utf-8"
    match = re.search(r"charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
    if match:
        encoding = match.group(1)
    html = raw.decode(encoding, errors="replace")
    parser = _ReadableTextParser()
    parser.feed(html)
    text = _collapse_text("\n".join(parser.text_parts))
    title = _collapse_text(" ".join(parser.title_parts), max_chars=300)
    if not text:
        return {"parse_status": "failed", "title": title, "text": "", "error": "empty_content"}
    return {
        "parse_status": "success",
        "title": title,
        "text": text,
        "content_type": content_type,
        "error": None,
    }


class TravelFetchUrlContentArgs(BaseModel):
    url: str = Field(..., description="Travel guide URL to fetch.")
    timeout_seconds: int | None = Field(default=None, description="HTTP timeout in seconds.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _travel_fetch_url_content(
    url: str,
    timeout_seconds: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = str(url or "").strip()
    if not url:
        return {"url": url, "parse_status": "failed", "title": "", "text": "", "error": "missing_url"}
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        return {"url": url, "parse_status": "failed", "title": "", "text": "", "error": "unsupported_url_scheme"}
    try:
        timeout = int(timeout_seconds) if timeout_seconds is not None else 8
    except (TypeError, ValueError):
        timeout = 8
    timeout = max(1, min(timeout, 20))
    try:
        payload = await asyncio.to_thread(_fetch_url_sync, url, timeout)
    except urllib.error.HTTPError as exc:
        payload = {"parse_status": "failed", "title": "", "text": "", "error": f"http_{exc.code}"}
    except urllib.error.URLError as exc:
        payload = {"parse_status": "failed", "title": "", "text": "", "error": str(exc.reason or exc)}
    except TimeoutError:
        payload = {"parse_status": "failed", "title": "", "text": "", "error": "timeout"}
    except Exception as exc:
        payload = {"parse_status": "failed", "title": "", "text": "", "error": exc.__class__.__name__}
    return {"url": url, **payload}


async def travel_fetch_url_content(args: dict[str, Any]) -> dict[str, Any]:
    return await _travel_fetch_url_content(
        url=str(args.get("url") or ""),
        timeout_seconds=args.get("timeout_seconds"),
        runtime_context={},
    )


travel_fetch_url_content_tool = StructuredTool.from_function(
    coroutine=_travel_fetch_url_content,
    name="travel_fetch_url_content",
    description=(
        "Fetch and extract readable text from a travel guide URL. "
        "Input: {url:string, timeout_seconds?:integer}. Failure returns parse_status=failed."
    ),
    args_schema=TravelFetchUrlContentArgs,
    infer_schema=False,
)
