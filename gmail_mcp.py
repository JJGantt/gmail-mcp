#!/usr/bin/env python3
"""MCP server exposing Gmail to agents.

Unlike the messages server this needs no Mac and no desktop app — it is ordinary
HTTPS against the Gmail API using a stored refresh token, so it runs anywhere:
this Mac, the Pi, or a Fly worker. Nothing here depends on a machine being awake.

Auth is a refresh token obtained once by `authorize.py`. Access tokens are
short-lived and refreshed automatically by the client library.

Message bodies are walked out of the MIME tree rather than read off a single
field, because a Gmail message is a part tree: the readable text can be at the
top level, inside multipart/alternative, or absent entirely with only HTML.
"""

import base64
import os
import pathlib
import re
from email.message import EmailMessage

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from mcp.server.mcpserver import MCPServer

load_dotenv(pathlib.Path(__file__).parent / ".env")

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/contacts.readonly",
]

mcp = MCPServer("gmail")

_services: dict = {}


def _service(name: str, version: str):
    """Cached API client built from the stored refresh token."""
    if name not in _services:
        creds = Credentials(
            token=None,
            refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
            client_id=os.environ["GOOGLE_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            token_uri=TOKEN_URI,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        _services[name] = build(name, version, credentials=creds,
                                cache_discovery=False)
    return _services[name]


def _gmail():
    return _service("gmail", "v1")


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "replace")


def _walk_body(payload: dict) -> str:
    """Best readable text from a MIME part tree, preferring plain over HTML."""
    plain, html = [], []

    def visit(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plain.append(_decode(data))
            elif mime == "text/html":
                html.append(_decode(data))
        for child in part.get("parts", []) or []:
            visit(child)

    visit(payload)
    if plain:
        return "\n".join(plain).strip()
    if html:
        text = re.sub(r"<br\s*/?>", "\n", "\n".join(html))
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    return ""


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}


def _summary(msg: dict) -> dict:
    h = _headers(msg)
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "from": h.get("from"),
        "to": h.get("to"),
        "subject": h.get("subject"),
        "date": h.get("date"),
        "snippet": msg.get("snippet"),
        "labels": msg.get("labelIds", []),
        "unread": "UNREAD" in msg.get("labelIds", []),
    }


# ---------------------------------------------------------------- read tools


@mcp.tool()
def search_email(query: str = "in:inbox", limit: int = 20) -> list[dict]:
    """Search mail using Gmail's own query syntax, newest first.

    Narrow with `query`; do not pull broadly and filter the results afterwards.
    Gmail evaluates the query server-side across the whole mailbox, so a precise
    query is both cheaper and more complete than sifting a wide page — a wide
    page is only the newest N of everything, so the message actually wanted may
    not be in it at all.

    This matters more than usual on this mailbox: of ~78k messages only ~3k are
    Primary, so an unfiltered `is:unread` is ~96% promotions and updates.

    Useful operators:
      category:primary        the Primary tab — usually what "my email" means
      category:promotions | social | updates | forums
      is:unread  is:starred  is:important
      from:  to:  subject:  has:attachment  larger:5M
      newer_than:2d  older_than:1y  after:2026/08/01
      in:inbox (default scope)  in:spam  in:trash  in:anywhere

    Spam and trash are excluded unless asked for by name.

    Args:
        query: a Gmail search string. Prefer the narrowest one that answers the
            question, e.g. "category:primary is:unread newer_than:3d".
        limit: how many messages to return.
    """
    listing = _gmail().users().messages().list(
        userId="me", q=query, maxResults=limit).execute()
    out = []
    for ref in listing.get("messages", []):
        msg = _gmail().users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"]).execute()
        out.append(_summary(msg))
    return out


@mcp.tool()
def get_email(message_id: str) -> dict:
    """Read one message in full, including its body text."""
    msg = _gmail().users().messages().get(
        userId="me", id=message_id, format="full").execute()
    data = _summary(msg)
    data["body"] = _walk_body(msg.get("payload", {}))
    return data


@mcp.tool()
def get_email_thread(thread_id: str) -> list[dict]:
    """Read every message in a conversation, oldest first."""
    thread = _gmail().users().threads().get(
        userId="me", id=thread_id, format="full").execute()
    out = []
    for msg in thread.get("messages", []):
        data = _summary(msg)
        data["body"] = _walk_body(msg.get("payload", {}))
        out.append(data)
    return out


@mcp.tool()
def list_labels() -> list[dict]:
    """List mailbox labels, with unread and total counts where Gmail reports them."""
    labels = _gmail().users().labels().list(userId="me").execute()
    out = []
    for ref in labels.get("labels", []):
        detail = _gmail().users().labels().get(
            userId="me", id=ref["id"]).execute()
        out.append({
            "id": detail["id"],
            "name": detail["name"],
            "unread": detail.get("messagesUnread"),
            "total": detail.get("messagesTotal"),
        })
    return out


@mcp.tool()
def find_email_contact(name: str, limit: int = 10) -> list[dict]:
    """Look up a contact's email addresses and phone numbers in Google Contacts."""
    people = _service("people", "v1")
    result = people.people().searchContacts(
        query=name, readMask="names,emailAddresses,phoneNumbers",
        pageSize=limit).execute()
    out = []
    for row in result.get("results", []):
        person = row.get("person", {})
        out.append({
            "name": next((n.get("displayName")
                          for n in person.get("names", [])), None),
            "emails": [e.get("value") for e in person.get("emailAddresses", [])],
            "phones": [p.get("value") for p in person.get("phoneNumbers", [])],
        })
    return out


# ---------------------------------------------------------------- write tools


def _build_mime(to: str, subject: str, body: str,
                cc: str | None, thread_headers: dict | None) -> dict:
    mime = EmailMessage()
    mime["To"] = to
    mime["Subject"] = subject
    if cc:
        mime["Cc"] = cc
    if thread_headers:
        # Threading is header-based: without these a reply starts a new
        # conversation even when threadId is set.
        if thread_headers.get("message-id"):
            mime["In-Reply-To"] = thread_headers["message-id"]
            mime["References"] = thread_headers["message-id"]
    mime.set_content(body)
    return {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode()}


@mcp.tool()
def send_email(to: str, subject: str, body: str, cc: str | None = None,
               reply_to_message_id: str | None = None) -> dict:
    """Send an email from the user's account.

    Args:
        to: recipient address, or comma-separated addresses.
        subject: subject line.
        body: plain text body, sent verbatim.
        cc: optional comma-separated CC addresses.
        reply_to_message_id: reply within that message's thread instead of
            starting a new conversation.
    """
    payload = None
    thread_id = None
    if reply_to_message_id:
        original = _gmail().users().messages().get(
            userId="me", id=reply_to_message_id, format="metadata",
            metadataHeaders=["Message-Id", "Subject"]).execute()
        thread_id = original.get("threadId")
        payload = _headers(original)

    message = _build_mime(to, subject, body, cc, payload)
    if thread_id:
        message["threadId"] = thread_id

    sent = _gmail().users().messages().send(userId="me", body=message).execute()
    return {"sent": True, "id": sent["id"], "thread_id": sent.get("threadId"),
            "to": to, "subject": subject}


@mcp.tool()
def create_draft(to: str, subject: str, body: str, cc: str | None = None) -> dict:
    """Save a draft without sending it."""
    draft = _gmail().users().drafts().create(
        userId="me", body={"message": _build_mime(to, subject, body, cc, None)}
    ).execute()
    return {"draft_id": draft["id"], "to": to, "subject": subject}


@mcp.tool()
def modify_email(message_id: str, add_labels: list[str] | None = None,
                 remove_labels: list[str] | None = None) -> dict:
    """Add or remove labels on a message.

    Archiving is removing "INBOX"; marking read is removing "UNREAD".

    Args:
        message_id: the message to change.
        add_labels: label ids to add.
        remove_labels: label ids to remove.
    """
    msg = _gmail().users().messages().modify(
        userId="me", id=message_id,
        body={"addLabelIds": add_labels or [],
              "removeLabelIds": remove_labels or []}).execute()
    return {"id": msg["id"], "labels": msg.get("labelIds", [])}


if __name__ == "__main__":
    mcp.run()
