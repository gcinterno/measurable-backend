"""Presentation-only renderer for Schedule Reports delivery email."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape


@dataclass(frozen=True)
class ScheduledReportEmailContext:
    title: str
    reporting_start: date
    reporting_end: date
    source_label: str
    generated_at: datetime
    locale: str
    view_url: str
    download_url: str
    preview_content_id: str = "measurable-report-preview"


@dataclass(frozen=True)
class RenderedScheduledReportEmail:
    subject: str
    html: str
    text: str


_MONTHS = {
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
    "es": ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
}


def _date(value: date, locale: str) -> str:
    month = _MONTHS[locale][value.month - 1]
    return f"{month} {value.day}, {value.year}" if locale == "en" else f"{value.day} de {month} de {value.year}"


def _period(start: date, end: date, locale: str) -> str:
    return f"{_date(start, locale)} – {_date(end, locale)}"


def _generated(value: datetime, locale: str) -> str:
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    day = _date(normalized.date(), locale)
    return f"{day} · {normalized:%H:%M} UTC"


def render_scheduled_report_email(context: ScheduledReportEmailContext) -> RenderedScheduledReportEmail:
    locale = "es" if context.locale == "es" else "en"
    copy = {
        "en": {
            "eyebrow": "SCHEDULED REPORT",
            "headline": "Your report is ready",
            "support": "Your scheduled marketing report has been generated successfully.",
            "period": "Reporting period",
            "source": "Source",
            "generated": "Generated",
            "view": "View report",
            "download": "Download PDF",
            "expiry": "Your private report link expires in 24 hours.",
            "footer": "AI Marketing Report Generator",
        },
        "es": {
            "eyebrow": "REPORTE PROGRAMADO",
            "headline": "Tu reporte está listo",
            "support": "Tu reporte de marketing programado se generó correctamente.",
            "period": "Periodo del reporte",
            "source": "Fuente",
            "generated": "Generado",
            "view": "Ver reporte",
            "download": "Descargar PDF",
            "expiry": "Tu enlace privado del reporte vence en 24 horas.",
            "footer": "Generador de Reportes de Marketing con IA",
        },
    }[locale]
    title = escape(context.title)
    source = escape(context.source_label)
    period = escape(_period(context.reporting_start, context.reporting_end, locale))
    generated = escape(_generated(context.generated_at, locale))
    view_url = escape(context.view_url, quote=True)
    download_url = escape(context.download_url, quote=True)
    cid = escape(context.preview_content_id, quote=True)

    html = f"""<!doctype html>
<html lang="{locale}">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
  <body style="margin:0;padding:0;background:#f5f7fa;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;background:#f5f7fa;">
      <tr><td align="center" style="padding:40px 16px;">
        <table role="presentation" width="588" cellspacing="0" cellpadding="0" border="0" style="width:100%;max-width:588px;">
          <tr><td style="padding:0 4px 20px;">
            <table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>
              <td width="32" height="32" align="center" valign="middle" style="width:32px;height:32px;border-radius:8px;background:#2563eb;color:#ffffff;font-size:16px;font-weight:800;line-height:32px;">M</td>
              <td style="padding-left:10px;color:#111827;font-size:18px;font-weight:700;letter-spacing:-0.2px;">Measurable</td>
            </tr></table>
          </td></tr>
          <tr><td style="background:#ffffff;border:1px solid #e6eaf0;border-radius:16px;padding:42px 42px 36px;">
            <div style="color:#2563eb;font-size:11px;font-weight:800;letter-spacing:1.4px;line-height:16px;">{copy['eyebrow']}</div>
            <h1 style="margin:12px 0 10px;color:#111827;font-size:30px;line-height:38px;font-weight:750;letter-spacing:-0.7px;">{copy['headline']}</h1>
            <p style="margin:0 0 28px;color:#5f6b7a;font-size:15px;line-height:24px;">{copy['support']}</p>
            <h2 style="margin:0 0 18px;color:#172033;font-size:20px;line-height:28px;font-weight:700;letter-spacing:-0.25px;">{title}</h2>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-bottom:24px;">
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['period']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{period}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['source']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{source}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['generated']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{generated}</td></tr>
            </table>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-bottom:24px;"><tr><td style="padding:8px;background:#f8fafc;border:1px solid #dfe5ec;border-radius:12px;">
              <img src="cid:{cid}" width="486" alt="{title}" style="display:block;width:100%;max-width:486px;height:auto;border:0;border-radius:7px;aspect-ratio:16/9;object-fit:contain;background:#ffffff;">
            </td></tr></table>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" align="center" style="margin:0 auto 18px;"><tr><td align="center" bgcolor="#2563eb" style="border-radius:9px;background:#2563eb;">
              <a href="{view_url}" style="display:inline-block;padding:13px 26px;color:#ffffff;font-size:15px;line-height:20px;font-weight:700;text-decoration:none;border-radius:9px;">{copy['view']}</a>
            </td></tr></table>
            <p style="margin:0;text-align:center;font-size:13px;line-height:20px;"><a href="{download_url}" style="color:#2563eb;font-weight:650;text-decoration:none;">{copy['download']}</a></p>
            <p style="margin:20px 0 0;text-align:center;color:#8a94a3;font-size:11px;line-height:17px;">{copy['expiry']}</p>
          </td></tr>
          <tr><td align="center" style="padding:24px 12px 0;color:#8a94a3;font-size:11px;line-height:17px;">
            <strong style="color:#536071;font-weight:700;">Measurable</strong><br>{copy['footer']}
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
    text = (
        f"{copy['headline']}\n\n{copy['support']}\n\n{context.title}\n"
        f"{copy['period']}: {_period(context.reporting_start, context.reporting_end, locale)}\n"
        f"{copy['source']}: {context.source_label}\n"
        f"{copy['generated']}: {_generated(context.generated_at, locale)}\n\n"
        f"{copy['view']}: {context.view_url}\n"
        f"{copy['download']}: {context.download_url}\n\n{copy['expiry']}\n\n"
        f"Measurable\n{copy['footer']}"
    )
    return RenderedScheduledReportEmail(subject=copy["headline"], html=html, text=text)
