"""Search, read and append to Notion pages.

Needs an internal integration token in .env as NOTION_TOKEN:
  1. https://www.notion.so/my-integrations -> New integration -> copy the
     "Internal Integration Secret" (starts with ntn_ or secret_)
  2. In Notion, open each page/database Ghost should see -> ... menu ->
     Connections -> add the integration. Notion shows an integration nothing
     until a page is explicitly shared with it, so this step is required.

Read/append only - no delete, and creating pages requires an explicit parent, so
Ghost can't scatter pages around the workspace on its own.
"""
import os
import requests
from dotenv import load_dotenv
from . import register

load_dotenv()

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"

def _headers():
    tok = os.getenv("NOTION_TOKEN")
    if not tok:
        return None
    return {"Authorization": f"Bearer {tok}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json"}

NO_TOKEN = ("Notion isn't connected yet. The user needs to create an internal "
            "integration at notion.so/my-integrations, put the secret in .env as "
            "NOTION_TOKEN, and share the relevant pages with that integration.")

def _title_of(obj):
    """Titles hide in different places depending on object type."""
    props = obj.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title") or []
            if parts:
                return "".join(p.get("plain_text", "") for p in parts)
    t = obj.get("title")
    if isinstance(t, list) and t:
        return "".join(p.get("plain_text", "") for p in t)
    return "(untitled)"

def _plain(block):
    """Flatten one block to text, ignoring types that carry none."""
    btype = block.get("type", "")
    payload = block.get(btype)
    if not isinstance(payload, dict):
        return ""
    rich = payload.get("rich_text")
    if not isinstance(rich, list):
        return ""
    text = "".join(r.get("plain_text", "") for r in rich).strip()
    if not text:
        return ""
    if btype == "to_do":
        return ("[x] " if payload.get("checked") else "[ ] ") + text
    if btype.startswith("heading"):
        return f"## {text}"
    if btype in ("bulleted_list_item", "numbered_list_item"):
        return f"- {text}"
    return text

@register({"name": "search_notion",
    "description": "Search the user's Notion workspace for pages by title/content. "
                    "Returns matching page titles and ids. Only finds pages that have "
                    "been shared with Ghost's Notion integration.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "what to search for"}},
        "required": ["query"]}})
def search_notion(query: str):
    h = _headers()
    if not h:
        return NO_TOKEN
    try:
        r = requests.post(f"{API}/search", headers=h, timeout=30,
                          json={"query": query, "page_size": 10,
                                "filter": {"property": "object", "value": "page"}})
        if r.status_code == 401:
            return "Notion rejected the token (401). It may be wrong or revoked."
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        return f"Notion search failed: {e}"
    if not results:
        return (f"No Notion pages matching '{query}'. Note Ghost only sees pages "
                "explicitly shared with its integration.")
    lines = []
    for p in results[:10]:
        lines.append(f"- {_title_of(p)}  [id: {p.get('id', '')}]")
    return f"{len(lines)} Notion page(s) matching '{query}':\n" + "\n".join(lines)

@register({"name": "read_notion_page",
    "description": "Read the text content of a Notion page by its id (get the id from "
                    "search_notion first).",
    "parameters": {"type": "object", "properties": {
        "page_id": {"type": "string"}}, "required": ["page_id"]}})
def read_notion_page(page_id: str):
    h = _headers()
    if not h:
        return NO_TOKEN
    pid = (page_id or "").strip()
    try:
        r = requests.get(f"{API}/blocks/{pid}/children", headers=h,
                         params={"page_size": 100}, timeout=30)
        if r.status_code == 404:
            return ("That page isn't visible to Ghost - it needs to be shared with the "
                    "Notion integration (page ... menu -> Connections).")
        r.raise_for_status()
        blocks = r.json().get("results", [])
    except Exception as e:
        return f"Couldn't read that Notion page: {e}"
    text = [t for t in (_plain(b) for b in blocks) if t]
    if not text:
        return "That page has no readable text (it may only contain embeds or databases)."
    return "\n".join(text)[:4000]

@register({"name": "append_to_notion_page",
    "description": "Append a paragraph of text to the end of an existing Notion page. "
                    "Use to add notes to a page the user names. Does not overwrite "
                    "anything.",
    "parameters": {"type": "object", "properties": {
        "page_id": {"type": "string"},
        "text": {"type": "string", "description": "the text to append"}},
        "required": ["page_id", "text"]}})
def append_to_notion_page(page_id: str, text: str):
    h = _headers()
    if not h:
        return NO_TOKEN
    body = {"children": [{
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}}]}
    try:
        r = requests.patch(f"{API}/blocks/{(page_id or '').strip()}/children",
                           headers=h, json=body, timeout=30)
        if r.status_code == 404:
            return ("That page isn't visible to Ghost - share it with the Notion "
                    "integration first.")
        r.raise_for_status()
    except Exception as e:
        return f"Couldn't append to that Notion page: {e}"
    return f"Added a note to the page ({len(text)} chars)."
