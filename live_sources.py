"""Opt-in live MCP check. No downloaded text/photos written to disk; not run in CI.
Run: uv run --locked python live_sources.py
"""
import asyncio
import base64
import io
import json
import sys
from pathlib import Path
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    report = {"protocol": "stdio MCP", "checks": []}
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name('server.py'))])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            assert 'browse_reference_catalog' in names
            report['tools'] = names

            async def call(name, args):
                response = await session.call_tool(name, args)
                assert not response.isError, response
                data = response.structuredContent or json.loads(response.content[0].text)
                assert data['status'] == 'ok', data
                return data

            async def check_image(url, entry_id=None):
                doc = await call('read_reference', {'url': url, 'entry_id': entry_id})
                assert doc['image_count'] > 0
                image_response = await session.call_tool('view_reference_image',
                    {'project_url': url, 'entry_id': entry_id, 'image_index': 0})
                image = next((c for c in image_response.content if c.type == 'image'), None)
                assert image is not None, [c.text for c in image_response.content if c.type == 'text']
                with Image.open(io.BytesIO(base64.b64decode(image.data))) as pixels:
                    pixels.load()
                    assert 1 <= max(pixels.size) <= 1600
                    size = pixels.size
                report['checks'].append({'source': doc['source'], 'title': doc['title'], 'url': url,
                                         'entry_id': entry_id, 'images': doc['image_count'],
                                         'decoded_image_size': size})
                print(json.dumps(report['checks'][-1], ensure_ascii=False), flush=True)

            async def try_image(url, entry_id=None):
                try:
                    await check_image(url, entry_id)
                except AssertionError as exc:
                    failure = {"url": url, "failure": str(exc)[:800]}
                    report['checks'].append(failure)
                    print(json.dumps(failure, ensure_ascii=False), flush=True)

            # Existing source, project cards, editorial article, two award entry formats.
            office = await call('search_references', {'query': 'акустика', 'limit': 1})
            await try_image(office['results'][0]['url'])
            await try_image('https://www.archdaily.com/1021878/jax-moc-offices-bricklab')
            await try_image('https://frameweb.com/project/max-show-office-hangzhou')
            journal = await call('search_references', {'query': 'office', 'source': 'architizer', 'limit': 1})
            await try_image(journal['results'][0]['url'])
            for source, query in [('architizer', 'Skylab'), ('waf', 'Acciona')]:
                catalog = await call('browse_reference_catalog', {'source': source, 'query': query, 'limit': 1})
                entry = catalog['results'][0]
                await try_image(entry['url'], entry['entry_id'])
            combined = await call('search_references', {'query': 'office', 'source': 'all', 'limit': 5})
            assert len({e['source'] for e in combined['results']}) >= 2
            assert any(r['status'] == 'host_search_required' for r in combined['source_reports'])
            report['combined_search'] = 'passed; native results and host suggestions distinguished'
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any("failure" in c for c in report["checks"]):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
