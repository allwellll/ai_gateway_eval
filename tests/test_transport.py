import asyncio
import json
import os
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web

from backend.transport import PublicResolver, UpstreamError, call_model


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        async def handle(request):
            self.received.append((request.path, dict(request.headers), await request.json()))
            if request.path == '/slow':
                await asyncio.sleep(.15)
                return web.json_response({'content':[{'type':'text','text':'21'}]})
            if request.path == '/redirect':
                raise web.HTTPFound('/v1/messages')
            if request.path.endswith('/messages'):
                return web.json_response({'content':[{'type':'thinking','thinking':'28'},{'type':'text','text':'21'}]})
            events = [
                {'type':'response.output_text.delta','delta':'最'},
                {'type':'response.output_text.delta','delta':'终答案：21'},
                {'type':'response.completed','response':{'status':'completed'}},
            ]
            response = web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request)
            for event in events:
                raw=('data: '+json.dumps(event,ensure_ascii=False)+'\n\n').encode()
                await response.write(raw[:12]); await response.write(raw[12:])
            await response.write_eof()
            return response
        app=web.Application(); app.router.add_post('/{path:.*}',handle)
        self.runner=web.AppRunner(app); await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0); await site.start()
        self.root='http://127.0.0.1:'+str(site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_real_http_json_sse_headers_timeout_and_redirect(self):
        resolver=PublicResolver()
        with patch.dict(os.environ,{'ALLOWED_PRIVATE_HOSTS':'127.0.0.1'}):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=resolver)) as session:
                response=await call_model(session,self.root+'/v1/responses','gpt-6-astra','dummy-key','hello','responses',2)
                self.assertEqual(response,'最终答案：21')
                response=await call_model(session,self.root+'/v1/messages','opus-5-5','dummy-key','hello','messages',2)
                self.assertEqual(response,'21')
                with self.assertRaises(UpstreamError):
                    await call_model(session,self.root+'/redirect','opus-5-5','dummy-key','hello','messages',2)
                with self.assertRaisesRegex(UpstreamError,'超时'):
                    await call_model(session,self.root+'/slow','opus-5-5','dummy-key','hello','messages',.03)
        await resolver.close()
        self.assertEqual(len(self.received),4, 'No retries or redirects may produce extra requests')
        for path,headers,payload in self.received:
            self.assertEqual(headers['User-Agent'],'curl/8.5.0')
            self.assertEqual(headers['Authorization'],'Bearer dummy-key')
            self.assertFalse(any(key in payload for key in ['max_tokens','max_output_tokens','max_completion_tokens','max_new_tokens']))
            self.assertTrue(payload['stream'])
        self.assertEqual(self.received[1][1]['x-api-key'],'dummy-key')
