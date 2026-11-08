"""Public-web tools for local models. Outbound web access is an owner opt-in."""
import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

import aiohttp
from aiohttp.resolver import ThreadedResolver

MAX_BYTES = 2_000_000


def enabled():
    return os.getenv("ENABLE_LOCAL_WEB", "").lower() == "true"


def localize_tools(definitions):
    out = []
    for tool in definitions:
        if "type" not in tool:
            out.append(tool)
        elif enabled() and tool["name"] in {"web_search", "web_fetch"}:
            search = tool["name"] == "web_search"
            key = "query" if search else "url"
            out.append({"name": tool["name"],
                        "description": "Search current public web pages." if search else "Read one public HTTP(S) page.",
                        "input_schema": {"type": "object", "properties": {key: {"type": "string"}}, "required": [key]}})
    return out


def public_address(value):
    ip = ipaddress.ip_address(value.split("%")[0])
    return ip.is_global and not ip.is_multicast and not (ip.version == 6 and ip.ipv4_mapped and not public_address(str(ip.ipv4_mapped)))


class PublicResolver(ThreadedResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await super().resolve(host, port, family)
        if not addresses or not all(public_address(a["host"]) for a in addresses):
            raise ValueError("Only public web hosts are allowed.")
        return addresses


async def read_public(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError("Use a public HTTP or HTTPS URL without credentials.")
    # aiohttp skips its resolver for literal IP addresses.
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal and not public_address(str(literal)):
        raise ValueError("Only public web hosts are allowed.")
    resolver = PublicResolver()
    try:
        # Check even if the underlying HTTP stack recognizes an unusual numeric IP representation.
        await resolver.resolve(parsed.hostname, parsed.port or 443, family=socket.AF_UNSPEC)
        connector = aiohttp.TCPConnector(resolver=resolver)
        async with aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    return ""
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        break
                if response.content_type == "application/pdf":
                    return ""
                text = body[:MAX_BYTES].decode(response.charset or "utf-8", errors="replace")
                return (_html_to_text(text) if "html" in response.content_type else text)[:20000].strip()
    finally:
        await resolver.close()


async def fetch(url):
    if not enabled():
        return "Local web access is disabled."
    try:
        text = await read_public(url)
        if not text and os.getenv("ENABLE_JINA_READER", "").lower() == "true":
            headers = {"Accept": "text/markdown"}
            if os.getenv("JINA_API_KEY"):
                headers["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get("https://r.jina.ai/" + url, headers=headers, allow_redirects=False) as response:
                    if response.status == 200:
                        text = (await response.content.read(80000)).decode(errors="replace")[:20000]
        return text or "No readable page content."
    except (ValueError, OSError, aiohttp.ClientError, TimeoutError, UnicodeError, LookupError):
        return "Could not read this public URL."


async def search(query):
    if not enabled():
        return "Local web access is disabled."
    try:
        from ddgs import DDGS
    except ImportError:
        return "Install requirements/web.txt to enable local web search."
    def results():
        return DDGS(timeout=10).text(query, max_results=5)
    hits = await asyncio.wait_for(asyncio.to_thread(results), timeout=25)
    urls = [hit for hit in hits if str(hit.get("href", "")).startswith(("http://", "https://"))]
    pages = await asyncio.gather(*(fetch(hit["href"]) for hit in urls[:3]))
    return "\n\n".join(f"{hit.get('title', '')}\n{hit['href']}\n{hit.get('body', '')}\n"
                       + (pages[i][:2000] if i < len(pages) else "") for i, hit in enumerate(urls)) or "No results."

def _html_to_text(html_content: str) -> str:
    """Extract readable text from HTML using stdlib."""
    import re
    from html import unescape
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip_depth += 1
            elif tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                self._parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "nav", "footer", "header"):
                self._skip_depth = max(0, self._skip_depth - 1)
            elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._parts.append("\n")

        def handle_data(self, data):
            if self._skip_depth == 0:
                self._parts.append(data)

    parser = _TextExtractor()
    parser.feed(html_content)
    text = unescape("".join(parser._parts))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
