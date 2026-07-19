import webbrowser
import requests
from bs4 import BeautifulSoup
from . import register

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

HEADERS = {"User-Agent": "Mozilla/5.0"}

@register({"name": "open_website",
    "description": "Open a website in the user's browser.",
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "full URL or site name like youtube.com"}},
        "required": ["url"]}})
def open_website(url: str):
    if not url.startswith("http"):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}."

@register({"name": "web_search",
    "description": "Search the web and return top results with titles, snippets and links. Use for any current info, prices, news, facts.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string"}}, "required": ["query"]}})
def web_search(query: str):
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    if not results:
        return "No results found."
    return "\n".join(f"- {r['title']}: {r['body'][:150]} ({r['href']})"
                     for r in results)

@register({"name": "read_webpage",
    "description": "Fetch a webpage and return its readable text. Use to read articles or compare info across pages.",
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string"}}, "required": ["url"]}})
def read_webpage(url: str):
    try:
        html = requests.get(url, headers=HEADERS, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer"]):
            t.decompose()
        text = " ".join(soup.get_text(" ").split())
        return text[:3000]
    except Exception as e:
        return f"Couldn't read page: {e}"