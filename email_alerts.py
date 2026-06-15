import os
from html import escape

from vertical_profiles import get_profile


def _lead_url(match: dict) -> str:
    return match.get("website_url") or f"https://{match.get('domain', '')}"


def _format_text(matches: list[dict], label: str = "OSI") -> str:
    lines = [f"New {label} domain leads: {len(matches)}", ""]
    for match in matches:
        lines.extend(
            [
                f"- {match.get('domain', '')}",
                f"  score: {match.get('score', '')} {match.get('score_category', '')}".rstrip(),
                f"  location: {match.get('location') or 'unknown'}",
                f"  website: {_lead_url(match)}",
                f"  phone: {match.get('phone') or ''}",
                f"  email: {match.get('email') or ''}",
                f"  reason: {match.get('reason') or ''}",
                "",
            ]
        )
    return "\n".join(lines)


def _format_html(matches: list[dict], label: str = "OSI") -> str:
    rows = []
    for match in matches:
        domain = escape(match.get("domain", ""))
        url = escape(_lead_url(match))
        rows.append(
            "<tr>"
            f"<td><a href=\"{url}\">{domain}</a></td>"
            f"<td>{escape(str(match.get('score') or ''))}</td>"
            f"<td>{escape(match.get('score_category') or '')}</td>"
            f"<td>{escape(match.get('location') or '')}</td>"
            f"<td>{escape(match.get('phone') or '')}</td>"
            f"<td>{escape(match.get('email') or '')}</td>"
            f"<td>{escape(match.get('reason') or '')}</td>"
            "</tr>"
        )

    return (
        f"<h2>New {escape(label)} domain leads</h2>"
        f"<p>{len(matches)} unalerted match(es) were found.</p>"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\">"
        "<thead><tr>"
        "<th>Domain</th><th>Score</th><th>Category</th><th>Location</th>"
        "<th>Phone</th><th>Email</th><th>Reason</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def send_match_alerts(matches: list[dict], profile=None) -> bool:
    """Send one digest email for newly matched domains, branded per vertical."""
    if not matches:
        return True

    if profile is None:
        profile = get_profile()
    label = profile.alert_label

    api_key = os.environ.get("RESEND_API_KEY") or os.environ.get("RESEND")
    alert_email = os.environ.get("ALERT_EMAIL") or "marleyhansenbarrett@gmail.com"
    sender = os.environ.get("ALERT_FROM") or "New Leads Notifications <alerts@learnripl.com>"

    if not api_key or not alert_email:
        print("[email_alerts] RESEND_API_KEY and ALERT_EMAIL are required to send alerts", flush=True)
        return False

    try:
        import resend

        resend.api_key = api_key
        resend.Emails.send(
            {
                "from": sender,
                "to": [alert_email],
                "subject": f"{len(matches)} new {label} domain lead(s)",
                "html": _format_html(matches, label),
                "text": _format_text(matches, label),
            }
        )
        print(f"[email_alerts] Sent digest for {len(matches)} lead(s) to {alert_email}", flush=True)
        return True
    except Exception as e:
        print(f"[email_alerts] Failed to send Resend alert: {e}", flush=True)
        return False
