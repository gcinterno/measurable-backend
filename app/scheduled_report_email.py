"""Presentation-only renderer and brand assets for Schedule Reports email."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path


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
    logo_content_id: str = "measurable-logo"


@dataclass(frozen=True)
class RenderedScheduledReportEmail:
    subject: str
    html: str
    text: str


# Official horizontal Measurable artwork on an email-safe white PNG canvas.
# The full 800x300 artwork is preserved; email markup controls width only.
MEASURABLE_LOGO_PNG = (Path(__file__).parent / "assets" / "measurable-email-logo.png").read_bytes()


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
            "chat": "Chat with your data",
            "download": "Download PDF",
            "expiry": "Your private PDF download link expires in 24 hours.",
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
            "chat": "Chatea con tus datos",
            "download": "Descargar PDF",
            "expiry": "Tu enlace privado de descarga del PDF vence en 24 horas.",
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
    logo_cid = escape(context.logo_content_id, quote=True)

    html = f"""<!doctype html>
<html lang="{locale}">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="color-scheme" content="light">
    <meta name="supported-color-schemes" content="light">
    <meta name="x-apple-disable-message-reformatting">
    <style>
      :root {{ color-scheme: light !important; supported-color-schemes: light !important; }}
      body, .email-bg, .email-shell, .email-card {{ background-color:#ffffff !important; }}
      [data-ogsc] .email-bg, [data-ogsc] .email-shell, [data-ogsc] .email-card {{ background-color:#ffffff !important; }}
      @media only screen and (max-width:620px) {{
        .email-pad {{ padding:24px 10px 32px !important; }}
        .email-card {{ padding:30px 20px 28px !important; }}
        .email-title {{ font-size:27px !important; line-height:34px !important; }}
        .email-logo {{ width:180px !important; max-width:180px !important; }}
        .action-button {{ width:100% !important; }}
        .metadata-value {{ padding-left:12px !important; }}
      }}
    </style>
  </head>
  <body bgcolor="#ffffff" style="margin:0;padding:0;background:#ffffff;background-color:#ffffff;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table class="email-bg" role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;background:#ffffff;background-color:#ffffff;">
      <tr><td class="email-pad" align="center" bgcolor="#ffffff" style="padding:32px 16px 40px;background:#ffffff;background-color:#ffffff;">
        <table class="email-shell" role="presentation" width="588" cellspacing="0" cellpadding="0" border="0" align="center" bgcolor="#ffffff" style="width:100%;max-width:588px;margin:0 auto;background:#ffffff;background-color:#ffffff;">
          <tr><td align="center" bgcolor="#ffffff" style="padding:0 0 24px;background:#ffffff;background-color:#ffffff;">
            <img class="email-logo" src="cid:{logo_cid}" width="190" alt="Measurable" align="center" style="display:block;width:190px;max-width:190px;height:auto;margin:0 auto;border:0;outline:none;text-decoration:none;">
          </td></tr>
          <tr><td class="email-card" bgcolor="#ffffff" style="background:#ffffff;background-color:#ffffff;border:1px solid #e4e8ee;border-radius:16px;padding:42px 42px 36px;">
            <div style="color:#2563eb;font-size:11px;font-weight:800;letter-spacing:1.4px;line-height:16px;">{copy['eyebrow']}</div>
            <h1 class="email-title" style="margin:12px 0 10px;color:#111827;font-size:30px;line-height:38px;font-weight:750;letter-spacing:-0.7px;">{copy['headline']}</h1>
            <p style="margin:0 0 28px;color:#5f6b7a;font-size:15px;line-height:24px;">{copy['support']}</p>
            <h2 style="margin:0 0 18px;color:#172033;font-size:20px;line-height:28px;font-weight:700;letter-spacing:-0.25px;">{title}</h2>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;margin-bottom:24px;background:#ffffff;background-color:#ffffff;">
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['period']}</td><td class="metadata-value" align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{period}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['source']}</td><td class="metadata-value" align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{source}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['generated']}</td><td class="metadata-value" align="right" style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{generated}</td></tr>
            </table>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;margin:0 0 24px;background:#ffffff;background-color:#ffffff;"><tr><td align="center" bgcolor="#ffffff" style="padding:0;text-align:center;background:#ffffff;background-color:#ffffff;">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" align="center" bgcolor="#f8fafc" style="width:100%;max-width:488px;margin:0 auto;background:#f8fafc;background-color:#f8fafc;border:1px solid #dfe4eb;border-radius:12px;"><tr><td align="center" bgcolor="#f8fafc" style="padding:9px;text-align:center;background:#f8fafc;background-color:#f8fafc;">
                <img src="cid:{cid}" width="468" alt="{title}" align="center" style="display:block;width:100%;max-width:468px;height:auto;margin:0 auto;border:0;border-radius:7px;background:#ffffff;background-color:#ffffff;outline:none;text-decoration:none;">
              </td></tr></table>
            </td></tr></table>
            <table class="action-button" role="presentation" width="240" cellspacing="0" cellpadding="0" border="0" align="center" style="width:240px;max-width:100%;margin:0 auto 12px;"><tr><td align="center" bgcolor="#2563eb" style="border:1px solid #2563eb;border-radius:9px;background:#2563eb;background-color:#2563eb;">
              <a href="{view_url}" style="display:block;padding:13px 20px;color:#ffffff;font-size:15px;line-height:20px;font-weight:700;text-align:center;text-decoration:none;border-radius:9px;">{copy['view']}</a>
            </td></tr></table>
            <table class="action-button" role="presentation" width="240" cellspacing="0" cellpadding="0" border="0" align="center" style="width:240px;max-width:100%;margin:0 auto 18px;"><tr><td align="center" bgcolor="#ffffff" style="border:1px solid #2563eb;border-radius:9px;background:#ffffff;background-color:#ffffff;">
              <a href="{view_url}" style="display:block;padding:12px 20px;color:#2563eb;font-size:15px;line-height:20px;font-weight:700;text-align:center;text-decoration:none;border-radius:9px;">{copy['chat']}</a>
            </td></tr></table>
            <p style="margin:0;text-align:center;font-size:13px;line-height:20px;"><a href="{download_url}" style="color:#2563eb;font-weight:650;text-decoration:underline;">{copy['download']}</a></p>
            <p style="margin:20px 0 0;text-align:center;color:#8a94a3;font-size:11px;line-height:17px;">{copy['expiry']}</p>
          </td></tr>
          <tr><td align="center" bgcolor="#ffffff" style="padding:24px 12px 0;color:#8a94a3;font-size:11px;line-height:17px;background:#ffffff;background-color:#ffffff;">
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
        f"{copy['chat']}: {context.view_url}\n"
        f"{copy['download']}: {context.download_url}\n\n{copy['expiry']}\n\n"
        f"Measurable\n{copy['footer']}"
    )
    return RenderedScheduledReportEmail(subject=copy["headline"], html=html, text=text)
