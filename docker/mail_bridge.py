#!/usr/bin/env python3
"""Minimal local SMTP server that relays mail to an HTTPS email API.

Why: Plane only sends email over SMTP, and Railway blocks outbound SMTP on the
Free, Trial and Hobby plans. Plane is pointed at this bridge
(EMAIL_HOST=127.0.0.1, EMAIL_PORT=2525, no TLS, no auth), and the bridge posts
each message to Resend, Postmark or Brevo over HTTPS, which works on every plan.

It listens on 127.0.0.1 only, so nothing outside the container can use it.
Standard library only (Python 3.12 in the Plane image).

Environment:
  MAIL_PROVIDER     resend (default) | postmark | brevo
  MAIL_API_KEY      provider API key / server token. Empty = log and drop mail.
  MAIL_FROM         optional sender override, e.g. "Plane <plane@example.com>".
                    Must be a sender/domain verified with the provider.
  MAIL_BRIDGE_PORT  default 2525
"""

import asyncio
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

HOST = "127.0.0.1"
PORT = int(os.environ.get("MAIL_BRIDGE_PORT", "2525") or 2525)
PROVIDER = (os.environ.get("MAIL_PROVIDER") or "resend").strip().lower()
API_KEY = (os.environ.get("MAIL_API_KEY") or "").strip()
FROM_OVERRIDE = (os.environ.get("MAIL_FROM") or "").strip()
MAX_MESSAGE_BYTES = 20 * 1024 * 1024
MAX_RECIPIENTS = 50
USER_AGENT = "plane-ce-railway-mail-bridge/1.0"


def log(msg):
    print(f"[mail-bridge] {msg}", flush=True)


# --------------------------------------------------------------- message ----
def _normalise(content):
    return content.replace("\r\n", "\n") if isinstance(content, str) else content


def parse_message(raw):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    attachments = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": part.get_filename() or "attachment",
                "content_type": part.get_content_type(),
                "content_b64": base64.b64encode(payload).decode("ascii"),
            }
        )
    return {
        "from": FROM_OVERRIDE or str(msg.get("From", "")).strip(),
        "reply_to": str(msg.get("Reply-To", "")).strip(),
        "subject": str(msg.get("Subject", "")).strip(),
        "html": _normalise(html_part.get_content()) if html_part is not None else None,
        "text": _normalise(text_part.get_content()) if text_part is not None else None,
        "attachments": attachments,
    }


# ------------------------------------------------------------- providers ----
def build_request(mail, recipients):
    if PROVIDER == "resend":
        body = {"from": mail["from"], "to": recipients, "subject": mail["subject"]}
        if mail["html"]:
            body["html"] = mail["html"]
        if mail["text"]:
            body["text"] = mail["text"]
        if mail["reply_to"]:
            body["reply_to"] = mail["reply_to"]
        if mail["attachments"]:
            body["attachments"] = [
                {"filename": a["filename"], "content": a["content_b64"]} for a in mail["attachments"]
            ]
        headers = {"Authorization": f"Bearer {API_KEY}"}
        return "https://api.resend.com/emails", headers, body

    if PROVIDER == "postmark":
        body = {
            "From": mail["from"],
            "To": ", ".join(recipients),
            "Subject": mail["subject"],
            "MessageStream": "outbound",
        }
        if mail["html"]:
            body["HtmlBody"] = mail["html"]
        if mail["text"]:
            body["TextBody"] = mail["text"]
        if mail["reply_to"]:
            body["ReplyTo"] = mail["reply_to"]
        if mail["attachments"]:
            body["Attachments"] = [
                {"Name": a["filename"], "Content": a["content_b64"], "ContentType": a["content_type"]}
                for a in mail["attachments"]
            ]
        headers = {"X-Postmark-Server-Token": API_KEY, "Accept": "application/json"}
        return "https://api.postmarkapp.com/email", headers, body

    if PROVIDER == "brevo":
        name, addr = parseaddr(mail["from"])
        sender = {"email": addr}
        if name:
            sender["name"] = name
        body = {"sender": sender, "to": [{"email": r} for r in recipients], "subject": mail["subject"]}
        if mail["html"]:
            body["htmlContent"] = mail["html"]
        if mail["text"]:
            body["textContent"] = mail["text"]
        if mail["reply_to"]:
            body["replyTo"] = {"email": parseaddr(mail["reply_to"])[1]}
        if mail["attachments"]:
            body["attachment"] = [{"name": a["filename"], "content": a["content_b64"]} for a in mail["attachments"]]
        headers = {"api-key": API_KEY, "Accept": "application/json"}
        return "https://api.brevo.com/v3/smtp/email", headers, body

    raise ValueError(f"unsupported MAIL_PROVIDER '{PROVIDER}' (use resend, postmark or brevo)")


def deliver(raw, recipients):
    """Return (ok, detail). Runs in a worker thread."""
    mail = parse_message(raw)
    if not API_KEY:
        log(f"MAIL_API_KEY not set; dropped email '{mail['subject']}' to {', '.join(recipients)}")
        return True, "dropped"
    url, headers, body = build_request(mail, recipients)
    headers.update({"Content-Type": "application/json", "User-Agent": USER_AGENT})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            log(f"sent '{mail['subject']}' to {', '.join(recipients)} via {PROVIDER} (HTTP {resp.status})")
            return True, "sent"
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace")
        log(f"{PROVIDER} rejected '{mail['subject']}' (HTTP {exc.code}): {detail}")
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log(f"could not reach {PROVIDER}: {exc}")
        return False, str(exc)


# ------------------------------------------------------------------ SMTP ----
def extract_address(arg):
    """'FROM:<a@b.c> SIZE=123' -> 'a@b.c'"""
    _, _, rest = arg.partition(":")
    rest = rest.strip()
    if rest.startswith("<"):
        return rest[1 : rest.find(">")] if ">" in rest else rest[1:]
    return rest.split(" ", 1)[0]


async def handle(reader, writer):
    async def reply(line):
        writer.write((line + "\r\n").encode("utf-8"))
        await writer.drain()

    mail_from, recipients = None, []
    try:
        await reply("220 localhost plane-mail-bridge ESMTP")
        while True:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=300)
            if not raw_line:
                break
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            verb, _, arg = line.partition(" ")
            verb = verb.upper()

            if verb == "EHLO":
                await reply("250-localhost")
                await reply(f"250-SIZE {MAX_MESSAGE_BYTES}")
                await reply("250-8BITMIME")
                await reply("250 SMTPUTF8")
            elif verb == "HELO":
                await reply("250 localhost")
            elif verb == "MAIL" and arg.upper().startswith("FROM:"):
                mail_from, recipients = extract_address(arg), []
                await reply("250 2.1.0 OK")
            elif verb == "RCPT" and arg.upper().startswith("TO:"):
                if mail_from is None:
                    await reply("503 5.5.1 MAIL first")
                elif len(recipients) >= MAX_RECIPIENTS:
                    await reply("452 4.5.3 Too many recipients")
                else:
                    recipients.append(extract_address(arg))
                    await reply("250 2.1.5 OK")
            elif verb == "DATA":
                if not recipients:
                    await reply("503 5.5.1 RCPT first")
                    continue
                await reply("354 End data with <CR><LF>.<CR><LF>")
                chunks, size, too_big = [], 0, False
                while True:
                    data_line = await asyncio.wait_for(reader.readline(), timeout=300)
                    if not data_line or data_line in (b".\r\n", b".\n"):
                        break
                    if data_line.startswith(b".."):
                        data_line = data_line[1:]
                    size += len(data_line)
                    if size > MAX_MESSAGE_BYTES:
                        too_big = True
                    elif not too_big:
                        chunks.append(data_line)
                if too_big:
                    await reply("552 5.3.4 Message too big")
                else:
                    loop = asyncio.get_running_loop()
                    try:
                        ok, detail = await loop.run_in_executor(None, deliver, b"".join(chunks), list(recipients))
                    except Exception as exc:  # never crash the server on one bad message
                        log(f"error while relaying: {exc!r}")
                        ok, detail = False, "internal error"
                    if ok:
                        await reply(f"250 2.0.0 OK {detail}")
                    else:
                        await reply(f"451 4.3.0 Relay failed: {detail}")
                mail_from, recipients = None, []
            elif verb == "RSET":
                mail_from, recipients = None, []
                await reply("250 2.0.0 OK")
            elif verb == "NOOP":
                await reply("250 2.0.0 OK")
            elif verb == "VRFY":
                await reply("252 2.0.0 Cannot verify")
            elif verb == "QUIT":
                await reply("221 2.0.0 Bye")
                break
            elif verb in ("STARTTLS", "AUTH"):
                await reply("502 5.5.1 Not supported: leave SMTP username/password empty and TLS/SSL off")
            else:
                await reply("502 5.5.2 Command not recognized")
    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    if PROVIDER not in ("resend", "postmark", "brevo"):
        log(f"WARNING: unsupported MAIL_PROVIDER '{PROVIDER}'; emails will fail (use resend, postmark or brevo)")
    server = await asyncio.start_server(handle, HOST, PORT, limit=1024 * 1024)
    log(f"listening on {HOST}:{PORT}, provider={PROVIDER}, api key {'set' if API_KEY else 'NOT set'}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
