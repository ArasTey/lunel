"""Subscription rendering and browser/client negotiation regressions."""
import asyncio
import base64
from html import unescape
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "console" / "api"))

from fastapi import FastAPI
import httpx
from lunel_console.services import gateway
from lunel_console.services.subscription import TELEGRAM_CONFIG, render_subscription

CONFIGS = [{"protocol": "vless-ws", "label": "Example", "share_url":
    "vless://00000000-0000-0000-0000-000000000001@example.test:443?security=tls&type=ws&path=%2Fi%2Ftoken%2Fws%2Fuuid&alpn=http%2F1.1#Example"}]


class SubscriptionTests(unittest.TestCase):
    def test_template_and_escaping(self):
        configs = [dict(CONFIGS[0], label='<script>alert(1)</script>')]
        page = render_subscription('<script>alert(1)</script>@@COUNT@@', configs, 'example.test', '/i/token/sub')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;@@COUNT@@', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('buildQrSvg', page)
        self.assertNotIn('cdnjs.cloudflare.com', page)
        self.assertNotIn('<?', page)
        self.assertIn('Remaining', page)
        self.assertNotIn('Not reported', page)
        self.assertIn('/i/token/ws/uuid', page)
        self.assertIn('http/1.1', page)
        self.assertIn(CONFIGS[0]['share_url'], unescape(page))
        matrix = json.loads(re.search(r'var QR_MATRIX = (.*);', page)[1])
        self.assertGreater(len(matrix), 20)
        self.assertTrue(all(len(row) == len(matrix) for row in matrix))

    def test_vmess_exports_and_page(self):
        data = {'v': '2', 'ps': 'VMess test', 'id': '123e4567-e89b-12d3-a456-426614174000',
                'add': 'example.test', 'port': '443', 'aid': '0', 'scy': 'auto', 'net': 'ws',
                'tls': 'tls', 'path': '/i/token/vmess-ws/uuid'}
        url = 'vmess://' + base64.b64encode(json.dumps(data).encode()).decode()
        self.assertEqual(gateway._singbox_outbound(url)['type'], 'vmess')
        self.assertEqual(gateway._clash_proxy(url)['network'], 'ws')
        page = render_subscription('VMess', [{'share_url': url}], 'example.test', '/i/token/sub')
        self.assertIn('VMess test', page)
        self.assertIn('/i/token/vmess-ws/uuid', page)
        self.assertIn(url, page)

    def test_xhttp_export_is_not_mislabeled_websocket(self):
        from fastapi import HTTPException
        url = CONFIGS[0]['share_url'].replace('type=ws', 'type=xhttp')
        for export in (gateway._singbox_outbound, gateway._clash_proxy):
            with self.assertRaises(HTTPException) as caught:
                export(url)
            self.assertEqual(caught.exception.status_code, 422)

    def test_channel_card_and_mobile_constraints(self):
        page = render_subscription('Mobile', CONFIGS, 'example.test', '/i/token/sub')
        self.assertIn('href="https://t.me/imArasTey"', page)
        self.assertIn(TELEGRAM_CONFIG, unescape(page))
        self.assertIn('not a working proxy', page)
        self.assertLess(page.index('Telegram Channel :'), page.index('id="link-2"'))
        self.assertIn('.cfg-card .name{display:block;', page)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', page)
        self.assertEqual(len(CONFIGS), 1)

    def test_empty_configs(self):
        page = render_subscription('Empty', [], 'example.test', '/i/token/sub')
        self.assertIn('No configurations available', page)
        self.assertIn('Configurations (0)', page)
        self.assertIn('∞', page)

    def test_usage_is_formatted_in_gb(self):
        page = render_subscription('Usage', CONFIGS, 'example.test', '/i/token/sub',
                                   usage={"used_bytes": 5 * 1024 ** 3, "limit_bytes": 0})
        self.assertIn('5.00 GB used', page)
        self.assertIn('>∞<', page)
        self.assertNotIn('Not reported', page)

        capped = render_subscription('Usage', CONFIGS, 'example.test', '/i/token/sub',
                                     usage={"used_bytes": 30 * 1024 ** 3,
                                            "limit_bytes": 50 * 1024 ** 3})
        self.assertIn('30.0 GB used', capped)
        self.assertIn('20.0 GB', capped)
        self.assertIn('width:60%', capped)

    def test_usage_survives_unlimited_and_tiny_values(self):
        page = render_subscription('Usage', CONFIGS, 'example.test', '/i/token/sub',
                                   usage={"used_bytes": 700 * 1024 ** 2, "limit_bytes": 1024 ** 2})
        self.assertIn('width:100%', page)
        self.assertIn('700.0 MB used', page)
        self.assertIn('0 B', page)
        self.assertNotIn('@@', page)

    def test_formats(self):
        asyncio.run(self._formats())

    async def _formats(self):
        app = FastAPI()
        app.include_router(gateway.router)
        pool = AsyncMock()
        pool.fetchrow.return_value = {'name': 'Example', 'public_host': 'example.test', 'status': 'running'}
        target = {'instance_id': 'example', 'worker_url': 'http://worker', 'upstream': '/proxy'}
        response = httpx.Response(200, json={'links': CONFIGS}, request=httpx.Request('POST', 'http://worker/share'))
        upstream = AsyncMock()
        upstream.__aenter__.return_value = upstream
        upstream.post.return_value = response
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://example.test') as client:
            with patch.object(gateway, '_resolve_endpoint', AsyncMock(return_value=target)), patch.object(gateway, 'get_pool', return_value=pool), patch('httpx.AsyncClient', return_value=upstream):
                browser = {'user-agent': 'Mozilla/5.0', 'accept': 'text/html'}
                for headers in ({}, {'user-agent': 'Mozilla/5.0'}, {'user-agent': 'Happ', 'accept': 'text/html'}):
                    result = await client.get('/i/token/sub', headers=headers)
                    self.assertEqual(base64.b64decode(result.text).decode().splitlines(), [TELEGRAM_CONFIG, CONFIGS[0]['share_url']])
                    self.assertIn('subscription-userinfo', result.headers)
                result = await client.get('/i/token/sub', headers=browser)
                self.assertIn('text/html', result.headers['content-type'])
                self.assertIn('Lunel Panel', result.text)
                result = await client.get('/i/token/sub?fmt=singbox', headers=browser)
                self.assertEqual(result.json()['outbounds'][0]['type'], 'vless')
                self.assertNotIn('imArasTey', result.text)
                self.assertEqual(len(result.json()['outbounds']), 1)
                result = await client.get('/i/token/sub?fmt=clash', headers=browser)
                self.assertIn('proxies:', result.text)
                self.assertNotIn('<!DOCTYPE', result.text)


class QuotaPolicyTests(unittest.TestCase):
    """Per-instance traffic quota and expiry reach Core's link policy."""

    def test_quota_and_duration_convert_to_policy(self):
        from lunel_console.routers.instances import CreateInstanceBody

        body = CreateInstanceBody({"name": "Prod", "config": {
            "protocol": "vless-ws", "quota_gb": 30, "duration_days": 30}})
        self.assertEqual(body.quota_bytes, 30 * 1024 ** 3)
        self.assertIsNotNone(body.expires_at)

        unlimited = CreateInstanceBody({"name": "Prod", "config": {"protocol": "vless-ws"}})
        self.assertEqual(unlimited.quota_bytes, 0)
        self.assertIsNone(unlimited.expires_at)

    def test_quota_rejects_out_of_range(self):
        from fastapi import HTTPException
        from lunel_console.routers.instances import CreateInstanceBody

        for bad in ({"quota_gb": -1}, {"quota_gb": 2000}, {"duration_days": -5},
                    {"duration_days": 4000}):
            with self.assertRaises(HTTPException):
                CreateInstanceBody({"name": "Prod", "config": {"protocol": "vless-ws", **bad}})

    def test_core_stops_expired_and_exhausted_links(self):
        """Core is what actually cuts the user off once quota or time runs out."""
        from datetime import datetime, timedelta, timezone

        from lunel_core.state import Link

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        self.assertTrue(Link("a", "L", "vless-ws").is_allowed())
        self.assertTrue(Link("b", "L", "vless-ws", limit_bytes=1024,
                             expires_at=future).is_allowed())
        self.assertFalse(Link("c", "L", "vless-ws", limit_bytes=1024,
                              used_bytes=1024).is_allowed())
        self.assertFalse(Link("d", "L", "vless-ws", expires_at=past).is_allowed())


if __name__ == '__main__':
    unittest.main()
