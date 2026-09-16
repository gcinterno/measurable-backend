"""Presentation-only renderer and brand assets for Schedule Reports email."""
from base64 import b64decode
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
    logo_content_id: str = "measurable-logo"


@dataclass(frozen=True)
class RenderedScheduledReportEmail:
    subject: str
    html: str
    text: str


# Official Measurable mark supplied for transactional email. This is a
# metadata-free, email-sized PNG crop of the source artwork, not a redraw.
MEASURABLE_LOGO_PNG = b64decode(
    """
iVBORw0KGgoAAAANSUhEUgAAAGAAAABSCAYAAACrKtGeAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAAB
AAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAYKADAAQAAAABAAAAUgAAAABy0rSoAAAWAUlEQVR4Ae1cC5hVxX2f
Oefce/cuy1tAhFQQm9AYtNVPbW3NF9NYDUZ8Lp+0KaJUQdPUEptYv/hYtTEP46O10foI8vkAI4o1KqiNn/ZpbKQlTWqAokCM
oIDssrvsvXvvOWf6+/3nzL0Lu+se4O6ywZ2POTNnZs5//v/f/zFz5p5FqaE0hMAQAkMIDCEwhMAQAkMIDCEwhMAQAh81BHQa
gRsXmbwaH83Kev6Ox67RL6d55tdpzLjG4tHlIPvNjKdXb3tUfVtrbQaKfy/NROHo+LIo9h/f3WleOLcpuvny+0wmzXO/DmMm
ftGcWoyyq8JIX1guh5fMuknlB5LvVAqI43haDJsIwzgw2rt+29bo0UtuNeMGktH+mGvcnPCSjs74WaX10SZSKoq9sLNdpYoK
teInlQLAUmSgAK2MisNIGe3Pbi6b58652cyoFSMDSeeYRpMde1H51s6yfjCO9UhtQgoHPQwkF3auVAqg9QN7RSUYXKJyqEKj
TyqXzapzmszMgWd7/2c8+stm3BYdPVIKg2tNbDwVw6BA7iBgL0KkUwAMxCWxEnAbhSEY15PKoXnyC9eZq1z/YC4nz+ucsf19
9Xxo/NkQAMYUV5EXI8P9AKdUCiBPtH6aCr3BGGiAJYQIY5UvRuqu078e3Y3FuX6A+U893bg/Ls5s7whWQY4TsZgJ8N7BiDl7
cZxaAQK4gE8FoILEIo5DeHEExXh//vYms/zzTR2T95rjoN6CRT1+TunKcjn7RBR5k2j5GlJjq8lLNfQksg00s6kUQLiZybCU
vHcMi9dW1oWZhY66lZ/9WukkDj/YaXKjyY+bHd1e6Az+Po7VME9Zy5cFN5FE5KEsYPZgrAOpFECMGXpiXBg2I5ZkmpmWRO7Z
X2JI0jM6Q//5U//aXHAwFTBlnjm83USPd0beIjANy8E+E0kMn3zvxRzbD4YGUilAAfQK4MK89QQKwfVA+lhHjhFfsZ8+rFSM
l/7uV6Jrmpqw0xjgNHmuObZ5d/xC7PmzFLaYVb+1TDrwGUorvFMu3gxwSgUOPYC8iRcIo/QGe29QYbsIUunjuqCyYeh967kP
4gc+d40ZOVByHdYYzmrtMC8Y5R0n8Z4TM9w7BpKK45fNUpf+yig3ut/LVAqgBwjIiScI+GzjPe2LwCd9rNMVuMXjS1uovEt3
dkRPn/RXZmp/SoN59ZjZpUVl4z0Or5xI8LvFGTJA/qqFveG95RvX9yttA1FJpQCtPfFO5wEV6we7VglWQex3Ejp5DEJSOfJP
K+w2q078C3NKfwh1wuWmfnRjeHexnLkjjEzeIOzI/LgIS+QKxi1tLKViOWG9yxj0TugPFnulmUoBERcxJ0DCsDCOi/UAHFG4
dnoEp6tIhSqUEBv9ifaieXbGleGf9MrNfnQccVHHx9ZuN091Rv6XZKGF5zlvdOQsr8kdPFWSrMaoQS4mGeP6bNOAXFMpoFSI
4tZmuLRnwa14AsyKO6KImwwqAPeiiC7hiO1MfHOGEsaUQm/JJy+Lbmp6xQS2Z/+vYxtLJ+0q1a2MlD4T8Q7z28kEU+EHPIEh
tlr+4KmoC49ocIqSfrLhmGV9gFIqBax+dZv3xss71Dsb2mTbST4ZeqIu2QpmdxUU0ApppRC56EFYmaPQBKH2bli6NHp4+tzW
sfsjJ8jrkeeHF3aU/eex7f2UBvhiyYmXCi8g7IB1uMo9LrwX8DnG3ZMR7ce5horvsqXfUyoFlCLt4/xE/d+anWr9mg/wJm9D
DxXgBAC2FaVIG1gXgZOSN/Qf5qiMxTny54TZhh/OuNxM3xcpua0ddWH52mKsH8Pb92GeWL6dRIRxk6LJKsLGmIpROH7Q7IzE
ycByoFMqBagMfn/JZpXK5dTWt3erX/xku+rowP4acVQWZEi6d2nd24YmCuWEc0pQOL6IIn1Ke0mt/K0F5dPTCH5Uoxl52//E
93eUgm8A3Swt32FGmCuKlxs2WKoCtPRbftjMNib3vPMg2zpw13QKyOaMKMGHIupyqmVHSa37z/dVa3NRQhI9QSzMlZDK2npV
ELHDirRshxfxRFKpqcVi8NTRl0RXoC7Dqk9Va+PPLU57p8M8XSx586k8xhCSY3bg0+IloVH6qBE02jpK3IshsN9lPMB+O0ie
HojL5zDJQ8gPplOAj/UyQKYnBMjwhEKHUuvf2Ka2v9suizMW2KoXQCIeW9hshXVSOQAIGr3BwIpjY4aXYnXP1HnRnb/H35/3
SqP+qPzp5vbgxXKoTzPlMgjalz8O0wmoYuUEFW2SpZ5sClhPMp9xdY6zqVe9uwG1LvlD1jzk+ekUEIDBDJXgWwVkEI7gCZEK
1DtvtqgtG1pkQY4BqXgCJKsAkggOkG0bZiUANiWnkVRCZPC+4F31Xot54uNzzST2Y5ge8eldc1sL0TPAfZoqw+MQ6xju2Eky
9LSK5fMhqjXpS26lcHXXx/cCjmWydKQ6UBfsGmwCqn0nE0BPPMPFQsytqIqQk9MrE5bVtk2tqlgI1fijRsNJfABAkaxgIh3r
VmLbiMdlCIcJKSgCDfSGYhh8oRCaVdMuKS/M/3TH6aVCfF3sdwbK78Rg0Fae3YnhOftwwgpvBfi95gaf9DRJ0p+wwCahwQJz
ywCqMp1NyvAaXFIpAOGHhgbOoQDs+ZMDdTSwjsLzVOu2gioXymriJ8bFmfocNidWySIYBedQlnwgUZClacVnk4xFfNe+P6Ot
Q/0ozvv5uK0ArwMo9EBJtHgGdq08H+2+tfjEHhIidqTRAeCMOmEs0JpC7JQZZFprQMmkiRjJBANapFO3gAy+IKzyGYYSQLIA
hesCwcliXWj31fZ1O5ZmfPO08QKEHIAFrFjyZdqGpQQEWnySpR3kbUDiD/9YnLWXHz5pjMofhh/ZeK7D2I/foiWzHyt/LLm6
1jCMyXaYazTBN/HbI4apBZ6vdzrL9jALhfZgDRRLkmWJVaO2bLVt+3ltbDLZY+YWfuMzTeleNJ1Zffh0Tk2wdEgFRKEEbn3E
okBCJMHFy6jOQulXw/P65rAU31VW/uWABMM41g5jTayVSkG9KjvqFTdABzRHhdQd1qA0wlpxW7uKyyV2IGfwHOZz48GXBBHy
wYwNQ84zrw9rKF/sNeSa1bbodo3QaburM7JGaizZtz8JLOipcwpHtpnMcaVOc+qza6JPBzoojdilTge9SqzvjXYqBehAG49W
BdAkjIBbWKjlmmgyo09idDaTee1OXQCYC6dfGr9VNvrmWAU5HpBxBIdVFtEECylcHSVxZXJrSW5EHkoIVOG9FhV3chfETmxD
YWSscv0QHmggsPysF6342Hh/wfr763ZMnG+O9GAvMoREJQknFdBJg5FVQqsd0Ov1hBPeyBRnTB+7dXduRlj2Th52vj4VHyYc
z5dCYYvyqfD1M0aq8mu9Uql2pFIA5ZKEGWh53F7KS1hXJYiEJEdJiId83ved6ZeGbxVDfS9CwjjDX6UwjkOZiZ7oLaErIUme
li47hsMw2EO4qztijOrc1qaiAtYFg51YMpfRsGN4nwdPyfnhnZOODK5df7fmqq0UhhJd7h0sZ2yUCWVutjMESl/iqRzRW9o6
5dhP7WrWjxfj4OOUgc/xMcpGkTVCb+Dp1huVjpt6I9Kl3UHbpannqkQfjGbJtY/Ye3RrWQhxA+EVQJKFsQuJtYuDp7K56CzE
3DfRKdZvFSDBSUCGHADB7u0JhqwJbGRy91Q65sqMH6m8EVgX8FuDKgHjEjwiBMBR3Jnzyos6nstcvcGBz+fxViH8si7JhSLI
APQIoGTWcexeN2yimzkZv2ex5anMGnxD+jUc0L+j6YX8kR8bcmtvoC3EvN2qiZz3nYBc30k8G4RFCVJCYIKfZLs4g5S8J4g8
exBd/0D2J6Mb9Ewg+08xQoQszjB9ARvWw+WE4c0qpqoA3jtlAH5ZyClVMHqU8kY12Ac74R1RuKM+G32x8GLdXQCgm+ACtABs
PYFWL7xjJKrWO9gGVynu3sqmD0natDwdPJPPmjsMAHG0haY4PR81rSDSjY+eiKZSAJF3SuCEoggYvHgBdkZWGVQAOrOZHif+
7+/pzfmMviDQ8QMG+3n8biwnCl2VIHWyDwrQiVVQwrVVBryGfVxEhtUrPW6CyjQE6xry+uz2l4c/2ZOAbCOi3PXYjHs0SLhg
CbalnwM9z+zowwM47PCLClMKJXUlYw9pOYFJh8SADwNfqpRKARxEDUsmw8hUAnekohjZnnIAwKG/95LWLdZtl067ZSFGfd14
GuszvcECvqe1ow0YSx9KeogF3rbzKIgLfq5e/+uYqSM/3/rqyB/3MqWqQ4cYDHlGnQ4iRuTacSPeAPbxz2HZGznViO9KmzuC
v42095uMp/YBUuVLIiggAZ/dvRLYq6N3tLoM9P1YwCfjDmuWAj5LtqNBJwrp8mi3alNTU7x1qX9rJojnAvQPsEOy4EISGrY7
1hbA0YatvVVAogR6jrY7nWXjh+lz3n9Eb+w2yV4Nbg0g/5LR7wyKnuDTO6gg4JgbmWC6Fw13+1wp/Eo5DmZpvjBSneCPiGsd
f4BaifTxe3irG99XmUoBCEDCoA8OnTVxIhGCjCfhiAoQM+trVvRveSR4vM5XZwOAtVwXaOWSHehcIzDOKYLKifAmjt1VlA3i
bx4z0b/4l0t1c19TFUkDmWA5nsE6G5JWlsmtrfZ6HXV++bRS5F8vli/IE30AYMzuhmy5cVjWvwxfLRd1WdkdWK+Uqh2pt6HC
b6IuUTpo6GQPyXAhYYlKSaVSy8C7y/RrU+cXzty5K7MEb86fkU/f0cWXKspHuixFCfAU7Pfb6/Lhot0rst/HVGSjz8QQxLch
xzMfoCKoALFWVKhcacOwTRzQQ5qCD7227DT3IMzUc9fD58kbd331mfj6lhX5V/jYqPM62/M537T1QKOnplRw4RvuxNpd/LQL
ryxqoEAXdyGJXrEvaeP385unNrSd53vRwzFcCC80Nv4D9Ur4wQsX1sctDcPi2R0rsg9S9n2Zg0IySjORPWa2kVe5lwXayoHm
bqmp6ZXgvZ3RHVi0pvNn1crkOKYPvGj58cd5d7uHWp7OrdiyzP9Hd99XST76TATYMSsljiMq4EMC2wZhnGR9UtxzwJolo1t+
+5P+/AzeXWBVYRlHHVxouR5EAD/wzE9xvHFW65PBqj2fTHkHxMgjjYQ8Oln4Bs01gPfShrKndMea378CPM0hU06RBh4ZqHDt
hBH+Va826T2OHJKX0J5IdWtLpQC335XtJoVIyAjjuCQLmBWk2xTpGijErhX+zTkdzgcmuyggM44VXhyRL57VslyvSUep+yjy
R56ZfWigK9hctqQPFypnyqZNaKmmsReUTu4oe3/Dgz458kCXgZdi0W2vz6uFmx/V+3t6R0eKUikAmyBhkkHPMeusifdWEdWy
yv6+1UDLtD6TfXhULjoPO5O3ckH80NQJzY0fLK9/d98o9TQa1DlBNYBYmTBUFIISAVCpKdVn+dVGexjci9A4AliJgkiEcb8u
iG5oWZ755+rofar9AKNPRD4plQLsPsJaCK3EunFiSQhH0gZqLGuRdqzIvDKpXv/BMRO9BesWj0u7nn341OCNi6ZLwmsifaWd
3xFvmiJDcP6k32mrv60c6d/hN0dONg9v+1k/emxmNvN3jtZ+lNvwzGrk/0q1C+IEjgHWrelYb+DpNDdDtCLyz/ORWqRNy/V7
m2pBiNugouW1YvLglYkFeUfIs1teHOrlj4dES5QaMas8vzP252E1Ep+h7njQ5pv454cPK169fFmDvA6i+YBSSg+wzLqZyDCZ
l0wBkhsXity4wVLyHaCnJJZPZJE4gu87J3/5pvL480rHFWP/21ZMe9zAH9Ww8Wgb0eBdsXFZQ82+4E2lgG6DEsCrjJN5q4hu
YznoYCa8iRHjxAe6cWL7rALwDWy08uIbRzd3evdg4z0Gp1VJ2IIKsZLXZcwN25fpf+tG5AAa0uEl6GIWmjrBT6zG3tir7DTQ
RysadElMucK0gFqJ+4k4vM/ih6ef7yzfFMb+KfygmD+jUjr8XTS+AouWnXFRdb9fKxlTrQFwPf7kgWSF4NWGIdTIIxJhlxfj
5F4aB8tFeKoyxpqVhAzSN3g0HqmOojm2WDTH4wMZCMSTVx5fZPAeEv/s8CP8v1w+W9ck7neFJaUC/C08DnBJYj7viT+lSSSS
NcANGmQlLVx4tWwLd2zjL8/WGwB4bHIx/u5W5GE7NqZ+ELfV13lXvPUPmjuXmqdU8SKf857BFwbbuQtwSaJR16edWXVtc4MP
cklb6WrzXe/EfnhB0rQyfn2BU0HDcxAImc9E1zU/of/djqj9NRVcax/Q6zOBmQeb+BWV4LAWwXAjyhAJcMF/vlB7NmtAsStX
5Nnxm5C23gGJeAaCj82wIuDLiuixM+ZkvleD2XsicTYaL0+lAD799pJg5Yj68plYD36s+a0oXVQ0wF7YF+vIHk7NpGHQXvbc
D4kiwHGFaX76ggOCTLlj7cRM5ur+iPuAZjjyrcj3pVYA8Xzzgdz/jpqsZ+IcZDHjI3+BkTjaRQAe7Q62xGguiWgjO8PhbQV4
DuBLZClSXlxuG1YfLNj4Q12z/T7Jd0mfRf0XyE/ukwJI4Gff0s2/fNj7s6wffxUCFOTQDLGTwjANQvwBbPIqRrRlZyP+K/zy
YpWAMVx/EfvzpnBjy0v5f6kMqH3lQpB8BvnafVYAeeFx6+ZH/e9mgtKF2I9u1vi7Aa5fFGS/CJJoPya7RlklcKdmE++t4UAe
NHEririvSz+YOXXSgZzzuAl6K49Ax1TkHyFvOCC8Nj9St3JkvnRG4JtXsV+zEw7SRVis3OKceCvPRa0K5IQURw2ZuvjNCXV6
0fLltd/vW3Dk+oe4TkH+DvKjB6QAEFDrFtetm1Dfci5+TLmXP0vi510esQ+exMM4AM8fXiRVCreJQAPWMk/HrfUjMgs2r56y
v+f7aWQmNhch34j8Debqxh53+5tW3z8GP6CoL03+0+gtz3gD9t8SpOKXv8ojEXc6gEuMOgybfBHjvi2f8a7ZtLi25zxuri4l
fldQryPz9wD8aVENQzbkMe8+4t8+vsG7nYQHY0qWYssaNYKk8UlH4Mf3b3xY3Wdb+vXKrzhuRhbwOVNNPICEXFp9v97l6oOp
JPj8ww7nBbJb4/m+F/1Hfoz/1X35HbeWctVcAbVkrma06J4ShKoUafm+jrfm8S0PPuZN/SFVlUJtage8CNeGjX6kwkVYwk01
AHH3gyU4RNy/asND+s1+nL1P0oe+AmQRhgb4+psUEveD+LsblujlfSLUzwMOeQXUwQPwEQlg5IsWd5zcJ0cvTRobcDE86OmQ
XwPoAFxw+R4gx+km3lhf7y/kn1EddPTBwCHvAYoagOXjN17G/UJ9Xbxw3YN642AAnzwc8gowmaLEH37B7fvmlg2LMy8NFvA/
Egqow59oAPzhgY6eOmqad9tgAp+8HPJrAL4i3JEL1C3aKz30alP9Hh/RDjZlDPEzhMAQAkMIDCEwhMAQAkMIDCEwhMAQAh8N
BP4f0pHY90fb8c0AAAAASUVORK5CYII=
    """
)


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
    logo_cid = escape(context.logo_content_id, quote=True)

    html = f"""<!doctype html>
<html lang="{locale}">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
  <body style="margin:0;padding:0;background:#ffffff;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="#ffffff" style="width:100%;background:#ffffff;">
      <tr><td align="center" style="padding:32px 16px 40px;">
        <table role="presentation" width="588" cellspacing="0" cellpadding="0" border="0" align="center" style="width:100%;max-width:588px;margin:0 auto;">
          <tr><td align="center" style="padding:0 0 24px;">
            <img src="cid:{logo_cid}" width="76" alt="Measurable" align="center" style="display:block;width:76px;max-width:76px;height:auto;margin:0 auto;border:0;outline:none;text-decoration:none;">
          </td></tr>
          <tr><td style="background:#ffffff;border:1px solid #e4e8ee;border-radius:16px;padding:42px 42px 36px;">
            <div style="color:#2563eb;font-size:11px;font-weight:800;letter-spacing:1.4px;line-height:16px;">{copy['eyebrow']}</div>
            <h1 style="margin:12px 0 10px;color:#111827;font-size:30px;line-height:38px;font-weight:750;letter-spacing:-0.7px;">{copy['headline']}</h1>
            <p style="margin:0 0 28px;color:#5f6b7a;font-size:15px;line-height:24px;">{copy['support']}</p>
            <h2 style="margin:0 0 18px;color:#172033;font-size:20px;line-height:28px;font-weight:700;letter-spacing:-0.25px;">{title}</h2>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin-bottom:24px;">
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['period']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{period}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['source']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{source}</td></tr>
              <tr><td style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#758091;font-size:12px;line-height:18px;">{copy['generated']}</td><td align="right" style="padding:11px 0;border-top:1px solid #edf0f4;border-bottom:1px solid #edf0f4;color:#273244;font-size:12px;line-height:18px;font-weight:600;">{generated}</td></tr>
            </table>
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="width:100%;margin:0 0 24px;"><tr><td align="center" style="padding:0;text-align:center;">
              <table role="presentation" width="486" cellspacing="0" cellpadding="0" border="0" align="center" style="width:100%;max-width:486px;margin:0 auto;"><tr><td align="center" style="padding:8px;background:#f7f8fa;border:1px solid #dfe4eb;border-radius:12px;text-align:center;">
                <img src="cid:{cid}" width="468" alt="{title}" align="center" style="display:block;width:100%;max-width:468px;height:auto;margin:0 auto;border:0;border-radius:7px;background:#ffffff;outline:none;text-decoration:none;">
              </td></tr></table>
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
