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
    "description": "Open a URL in the user's browser. This is the RIGHT way to reach "
                    "a feature inside a web app - navigate straight to its URL rather "
                    "than scrolling or clicking around whatever is currently on "
                    "screen. A new ChatGPT chat is chatgpt.com, a new Claude chat is "
                    "claude.ai/new, a new Gmail compose is mail.google.com. Prefer "
                    "this over scroll_window / click_control / read_window_text for "
                    "anything that is a website.",
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

# Sites that are useless over plain HTTP: they need the user's logged-in session
# and render their content with JavaScript, so a cookie-less fetch returns a
# login page or an empty shell - never the user's actual data.
_NEEDS_LOGIN = ("claude.ai", "chatgpt.com", "chat.openai.com", "mail.google.com",
                "gmail.com", "outlook.office.com", "notion.so", "x.com",
                "twitter.com", "facebook.com", "instagram.com", "linkedin.com",
                "discord.com", "web.whatsapp.com", "moodle", "monash.edu",
                # FT article bodies are paywalled - fetching one returns a teaser
                # that reads like the article. Use the ft_news skill instead.
                "ft.com")


@register({"name": "read_webpage",
    "description": "Fetch a PUBLIC webpage and return its readable text. Use to read "
                    "articles or compare info across pages. This is an anonymous "
                    "fetch with no login - it CANNOT read anything behind a sign-in, "
                    "so it cannot see the user's own ChatGPT or Claude conversations, "
                    "inbox, Notion pages or social feeds. Don't use it for those and "
                    "then guess at the contents.",
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string"}}, "required": ["url"]}})
def read_webpage(url: str):
    # Say this plainly rather than returning a login page as if it were content -
    # that is how an assistant ends up inventing "your previous conversation".
    low = (url or "").lower()
    if any(s in low for s in _NEEDS_LOGIN):
        return (f"Can't read {url} this way - it needs the user's logged-in session, "
                "and this fetch is anonymous, so it would only see a login page. "
                "Tell the user plainly that you cannot read their signed-in content "
                "on that site, and do NOT guess at what it might contain. You can "
                "open_website the URL so they can look at it themselves.")
    try:
        html = requests.get(url, headers=HEADERS, timeout=10).text
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer"]):
            t.decompose()
        text = " ".join(soup.get_text(" ").split())
    except Exception as e:
        text = ""
        err = str(e)
    else:
        err = ""
    # A JS-rendered page returns an near-empty shell to a plain fetch. Jina
    # Reader renders it server-side and hands back clean text, no key needed.
    if len(text) < 200:
        try:
            r = requests.get("https://r.jina.ai/" + url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            rendered = " ".join(r.text.split())
            if len(rendered) > len(text):
                return rendered[:3000]
        except Exception:
            pass
    if not text:
        return f"Couldn't read page: {err or 'no readable text'}"
    return text[:3000]