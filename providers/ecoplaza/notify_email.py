#!/usr/bin/env python3
"""Email a report through a plain SMTP server.

The weekly ecoplaza sync calls this at the end (see cron-sync.sh) with the
report on stdin; the credentials come from the environment, never from the
repo -- inside the container they are the SMTP_* keys of docker/pro.env,
which entrypoint.sh copies to /etc/almaixera-sync.env:

    SMTP_HOST       server to talk to (empty or unset: notification disabled)
    SMTP_PORT       port to connect to (default 587)
    SMTP_USER       account to authenticate (and send) as
    SMTP_PASSWORD   account password (for Gmail: an app password)
    SMTP_TO         recipient; defaults to SMTP_USER

    $ tail -n 20 ecoplaza-sync.log | notify_email.py --subject "sync failed"

Exit status is 0 when the report was sent, and also 0 (with a note on
stdout) when SMTP_HOST is not configured, so callers can invoke it
unconditionally.
"""
from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email.message import EmailMessage


def send_report(subject: str, body: str) -> None:
    """Send ``body`` as a plain-text email, or return early if disabled."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        print("notify_email: SMTP_HOST not set, notification disabled")
        return
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    to = os.environ.get("SMTP_TO", "").strip() or user
    port = int(os.environ.get("SMTP_PORT", "587"))
    if not (user and password and to):
        raise SystemExit(
            "notify_email: SMTP_USER, SMTP_PASSWORD and SMTP_TO "
            "(or just SMTP_TO) must all be set")

    message = EmailMessage()
    message["From"] = user
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=60) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Email stdin as a report; configured through the "
                    "SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_TO "
                    "environment variables.")
    parser.add_argument("--subject", required=True, help="email subject line")
    args = parser.parse_args(argv)
    try:
        send_report(args.subject, sys.stdin.read())
    except Exception as exc:  # the caller logs this and moves on
        print(f"notify_email: could not send the report: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
