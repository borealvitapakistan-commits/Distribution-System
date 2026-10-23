"""Allow basic description formatting while excluding executable HTML."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


class DescriptionParser(HTMLParser):
    allowed = {"p", "div", "span", "br", "strong", "b", "em", "i", "u", "s",
               "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "a", "table",
               "thead", "tbody", "tr", "th", "td"}
    blocked = {"script", "style", "iframe", "object", "svg", "math", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked or self.suppressed:
            self.suppressed.append(tag)
            return
        if tag not in self.allowed:
            return
        attributes = ""
        if tag == "a":
            href = dict(attrs).get("href") or ""
            href = "".join(c for c in href.strip() if ord(c) >= 32)
            try:
                safe = urlsplit(href).scheme.lower() in {"", "http", "https", "mailto"}
            except ValueError:
                safe = False
            if href and safe:
                attributes = f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
        self.parts.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag in self.suppressed:
                self.suppressed = self.suppressed[:self.suppressed.index(tag)]
            return
        if tag in self.allowed and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(escape(data))


def sanitize_description(value):
    parser = DescriptionParser()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.parts)
