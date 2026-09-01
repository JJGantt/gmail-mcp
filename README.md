# gmail-mcp

MCP server exposing Gmail to agents — search, read, label, draft and send.

Unlike `messages-mcp` this has **no machine dependency**. It is plain HTTPS
against the Gmail API with a stored refresh token, so it runs on the Mac, the Pi
or a Fly worker equally, and works while every one of them is asleep.

## Setup

One-time, and steps 1–4 have to happen in a browser because Google has no API
for creating its own OAuth clients.

1. **Create a project** at <https://console.cloud.google.com/projectcreate>.
2. **Enable the APIs** — Gmail API, and People API for contact lookup.
3. **Configure the OAuth consent screen** as *External*, declare the three
   scopes below, and then **set the publishing status to "In production"**.

   Production is not optional here. An external app left in **Testing** status
   is issued refresh tokens that Google revokes after **7 days**, which would
   take the integration down weekly with no signal beyond a sudden
   `invalid_grant`. Publishing status is the only thing that governs this;
   verification does not enter into it.

   Going to production *unverified* is the right call for personal use. It costs
   an "unverified app" interstitial at consent — click Advanced, then continue —
   and caps the app at 100 users. Verification exists to remove that warning for
   strangers, and there are no strangers here. Requesting it for restricted
   Gmail scopes means a security assessment, for no benefit to a single-user
   app.
4. **Create credentials → OAuth client ID → Desktop app**, and download the JSON.
5. Exchange it for a refresh token:

   ```
   ./.venv/bin/python authorize.py ~/Downloads/client_secret_*.json
   ```

   It prints a URL instead of opening a browser, because it normally runs in a
   terminal that isn't the browser's context. Approve, and it writes `.env`
   (mode 600, gitignored) holding the client id, secret and refresh token.

## Scopes

| Scope | Why |
|---|---|
| `gmail.modify` | read, search, label, archive, trash, draft **and send** |
| `contacts.readonly` | resolve names to addresses |

Two scopes, not more, because `gmail.modify` is already every read and write
operation short of permanent deletion — Google describes it as "read, compose,
and send." Adding `gmail.send` alongside it, as this first did, grants nothing.

What is deliberately *not* requested matters more than what is:

- **`https://mail.google.com/`** — the only scope that permanently deletes,
  bypassing the trash. Without it, the worst an agent can do to this mailbox is
  recoverable from Trash for 30 days.
- **`gmail.settings.basic` / `gmail.settings.sharing`** — filters, forwarding
  rules, send-as identities, delegation. These are the settings an attacker
  changes to persist in a mailbox, and nothing here needs them.

`gmail.modify` is a *restricted* scope, so it is the reason production status
goes unverified rather than verified (verification of a restricted scope means a
security assessment).

## Tools

| Tool | What it does |
|---|---|
| `search_email` | Gmail query syntax — `is:unread`, `from:x`, `newer_than:2d` |
| `get_email` | One message with its body text |
| `get_email_thread` | Every message in a conversation |
| `list_labels` | Labels with unread and total counts |
| `find_email_contact` | Look a person up in Google Contacts |
| `send_email` | Send, optionally as a reply within a thread |
| `create_draft` | Save without sending |
| `modify_email` | Add/remove labels — archive is removing `INBOX` |

## Two things worth knowing

**A message body is a tree, not a field.** Readable text can sit at the top
level, inside `multipart/alternative`, or not exist at all with only HTML
present. `_walk_body` visits the whole part tree and prefers plain text,
falling back to stripped HTML, so mail from senders who ship HTML only still
reads as text.

**Replying needs headers, not just a thread id.** Setting `threadId` alone still
produces a new conversation in most clients; `In-Reply-To` and `References` are
what actually thread it. `send_email` fetches the original's `Message-Id` and
sets both.

## What still revokes the token

Publishing to production removes the 7-day clock but not these, which are worth
knowing because each one looks identical from the outside — an `invalid_grant`
and a dead integration:

- **Changing the Google account password.** Refresh tokens carrying Gmail scopes
  are revoked on password change. This is the most likely cause in practice.
- **Six months without use.**
- **Revoking access** at <https://myaccount.google.com/permissions>.
- **More than 100 live tokens** for this client, which invalidates the oldest.

Re-running `authorize.py` fixes all of them.

## Not in scope

No watcher and nothing reacts to mail arriving. These are on-demand tools an
agent calls. Reacting to new mail would use Gmail's `users.watch` push into a
Pub/Sub topic delivered to a public HTTPS endpoint — which is why that leg would
live on Fly rather than here, and why it needs re-arming every 7 days.
