"""Render the public subscription page without exposing Core API credentials."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import qrcode


TELEGRAM_CONFIG = "vless://%40imArasTey@t.me/imArasTey:1?security=none&encryption=&host=t.me%2FimArasTey&type=ws#Telegram%20Channel%20%3A%20%40imArasTey"


@lru_cache(maxsize=1)
def _template() -> str:
    return Path(__file__).with_name("subscription.html").read_text(encoding="utf-8")


def _config_card(config: dict, number: int) -> str:
    url = str(config["share_url"])
    if url.startswith("vmess://"):
        import base64
        data = json.loads(base64.b64decode(url[8:] + "=" * (-len(url[8:]) % 4)))
        params = {"type": "ws", "security": data.get("tls", ""), "path": data.get("path", ""),
                  "sni": data.get("sni", ""), "alpn": data.get("alpn", "http/1.1")}
        parsed = urlsplit("vmess://" + str(data["id"]) + "@" + str(data["add"]) + ":" + str(data["port"]))
        vmess_name = str(data.get("ps") or config.get("label") or "VMess")
    else:
        parsed = urlsplit(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        vmess_name = ""
    protocol = {"ss": "SS", "vless": "VLESS", "trojan": "TROJAN"}.get(
        parsed.scheme, parsed.scheme.upper()
    )
    network = params.get("type", "")
    transport = network.upper()
    if parsed.scheme == "ss":
        plugin = params.get("plugin", "").split(";")
        options = dict(part.split("=", 1) for part in plugin if "=" in part)
        network = options.get("mode", "")
        transport = "WS" if network == "websocket" else "AEAD"
        params.update({"path": options.get("path", ""),
                       "security": "tls" if "tls" in plugin else ""})
    host = parsed.hostname or ""
    port = str(parsed.port or 443)
    name = vmess_name or unquote(parsed.fragment) or str(config.get("label") or f"Config {number}")
    flags = re.findall(r"[\U0001f1e6-\U0001f1ff]{2}", name)
    flag = f'<span class="flag">{escape(flags[0])}</span>' if flags else ""
    display_name = re.sub(r"[\U0001f1e6-\U0001f1ff]{2}", "", name).strip(" |") or name
    details = [("Protocol", protocol), ("Host", host), ("Port", port),
               ("Security", params.get("security", "")), ("Network", network)]
    details.extend((label, params.get(key, "")) for label, key in (
        ("SNI", "sni"), ("FP", "fp"), ("Mode", "mode"), ("Path", "path"),
        ("ALPN", "alpn"), ("Allow Insecure", "allowInsecure")))
    detail_html = "".join(
        f'<div class="detail-item"><label>{label}</label><span>{escape(value)}</span></div>'
        for label, value in details if value
    )
    badges = f'<span class="badge {escape(protocol.lower())}">{escape(protocol)}</span>'
    if transport and transport != protocol:
        badges += f'<span class="badge {escape(transport.lower())}">{escape(transport)}</span>'
    if url == TELEGRAM_CONFIG:
        host, port = "t.me/imArasTey", "1"
        badges = '<span class="badge ws">CHANNEL</span>'
        detail_html = '<p class="channel-note">Channel information only — not a working proxy.</p>'
    return f'''<div class="cfg-card tz" data-num="{number}">
     <div class="head" data-toggle="cfg">
      <span class="num">#{number}</span>{flag}
      <span class="info"><span class="name">{escape(display_name)}</span>
       <span class="host">{escape(host)}:{escape(port)}</span></span>
      <span class="badges">{badges}</span>
      <span class="arrow"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg></span>
     </div>
     <div class="acc"><div class="clip"><div class="inner cfg-inner">
      <div class="details-grid">{detail_html}</div>
      <div class="link-row">
       <input type="text" readonly value="{escape(url)}" id="link-{number}">
       <button type="button" class="copy-btn copy-single" data-num="{number}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9v3"/></svg>
        <span class="lbl">Copy</span>
       </button>
      </div>
     </div></div></div>
    </div>'''


def format_usage(value: int) -> str:
    """Human-readable traffic amount that grows past GB for heavy use."""
    value = max(0, int(value or 0))
    if value >= 1024 ** 4:
        return f"{value / 1024 ** 4:.2f} TB"
    if value >= 1024 ** 3:
        gb = value / 1024 ** 3
        return f"{gb:.2f} GB" if gb < 10 else f"{gb:.1f} GB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def render_subscription(title: str, configs: list, host: str, sub_path: str,
                        qr_path: str = "", usage: dict | None = None) -> str:
    """Keep browser-announced hosts when clients import the copied subscription."""
    sub_url = f"https://{host}{sub_path}?{urlencode({'host': host})}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    qr.add_data(sub_url)
    qr.make(fit=True)
    now = datetime.now(timezone.utc)
    cards = [{"share_url": TELEGRAM_CONFIG}] + [c for c in configs if c["share_url"] != TELEGRAM_CONFIG]
    used = int((usage or {}).get("used_bytes") or 0)
    limit = int((usage or {}).get("limit_bytes") or 0)
    used_text = f"{format_usage(used)} used"
    if limit > 0:
        remaining = max(0, limit - used)
        remaining_text = format_usage(remaining)
        bar_pct = max(2, min(100, round(used / limit * 100)))
        bar_color = "var(--green)" if used < limit * 0.9 else "var(--amber)"
    else:
        remaining_text = "∞"
        bar_pct = 100
        bar_color = "var(--blue)"
    if usage and not configs and int((usage.get("expired") or 0)) > 0:
        remaining_text = "Expired"
        bar_pct = 0
        bar_color = "var(--danger)"
        used_text = "All configs expired or over quota"
    values = {
        "BRAND": "Lunel",
        "TITLE": escape(title),
        "SUB_URL": escape(sub_url),
        "SUB_URL_JSON": json.dumps(sub_url).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"),
        "QR_MATRIX": json.dumps(qr.get_matrix(), separators=(",", ":")),
        "COUNT": str(len(configs)),
        "CONFIG_CARDS": "\n".join(_config_card(c, i) for i, c in enumerate(cards, 1))
            + ('' if configs else '<div class="empty-configs">No configurations available</div>'),
        "YEAR": str(now.year),
        "UPDATED": now.strftime("%Y/%m/%d %H:%M:%S UTC"),
        "USED_TEXT": escape(used_text),
        "REMAINING_TEXT": escape(remaining_text),
        "BAR_WIDTH": f"{bar_pct}%",
        "BAR_COLOR": bar_color,
    }
    return re.sub(r"@@([A-Z_]+)@@", lambda match: values[match[1]], _template())
