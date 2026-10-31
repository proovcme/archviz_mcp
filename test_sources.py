"""Synthetic fixtures only: no downloaded pages or reference archive in the repository."""
import asyncio
import html
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from bs4 import BeautifulSoup
import server
import sources


def soup(text):
    return BeautifulSoup(text, "html.parser")


AD = "https://www.archdaily.com/123/example-office"
FRAME = "https://frameweb.com/project/example-office"
AWARDS = "https://winners.architizer.com/2025/"
AD_IMG = "https://images.adsttc.com/media/images/test/photo.jpg"
FRAME_IMG = "https://d1tm14lrsghf7q.cloudfront.net/public/media/1/photo.jpg"
AWARD_IMG = "https://images.architizer.com/media/test/photo.jpg"
WAF_IMG = "https://evessio.s3.amazonaws.com/customer/b4289942-d924-4d3d-9044-2b4131d4ae91/event/test.png"


def frame_page(content, **extra):
    payload = {"props": {"content": content, **extra}}
    return soup('<div data-page="' + html.escape(json.dumps(payload), quote=True) + '"></div>')


def award_page():
    return soup(f'''<h4>Office interiors</h4>
    <div class="winner-container" data-types="Jury Winner"><div class="winner card"
      data-id="award.1" data-name="Test Office" data-url="/projects/test-office/"
      data-description="Acoustic ceiling." data-image="{AWARD_IMG}">
      <div class="card-body"><a href="/firms/example/">Example Studio</a></div></div></div>
    <div class="winner-container" data-types="Finalist"><div class="winner card"
      data-id="award.2" data-name="Other Office" data-url="/projects/other/"
      data-description="Different project" data-image="https://images.architizer.com/media/other.jpg">
      <div class="card-body"><a href="/firms/other/">Other Studio</a></div></div></div>''')


class SourceParsers(unittest.TestCase):
    def test_allowlist_rejects_ssrf_and_unimplemented_routes(self):
        for url in ["https://localhost/123/project", "https://archdaily.com.evil.test/123/project",
                    "http://frameweb.com/project/example", "https://a:b@frameweb.com/project/example",
                    "https://frameweb.com:444/project/example", "https://frameweb.com/article/example",
                    "https://architizer.com/projects/example/", "https://winners.architizer.com/admin/",
                    "https://frameweb.com/project/%2e%2e", "https://frameweb.com/project/../account"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                server.safe_url(url, "project")

    def test_image_provider_boundary_and_extension(self):
        self.assertIsNone(sources.image_entry(AD_IMG, "frame"))
        for url in [AD_IMG.replace('.jpg', '.svg'), 'https://images.adsttc.com/account/a.jpg',
                    'https://evil.test/media/images/a.jpg', AD_IMG.replace('test/', '%2e%2e/')]:
            with self.assertRaises(ValueError):
                sources.safe_url(url, "image")

    def test_officenext_gallery_stays_on_own_source(self):
        doc = server.parse_project(soup(f'<h1>Test</h1><article class="article"><p>Test</p></article>'
                                       f'<div class="gallery"><a href="{AD_IMG}"></a></div>'),
                                   'https://www.officenext.ru/projects/project-1-test/')
        self.assertEqual(doc['image_count'], 0)

    def test_archdaily_gallery_can_be_outside_article_and_excludes_ads(self):
        doc = sources.parse_archdaily(soup(f'''<h1>Example</h1><article id="single-content">
            <p>Acoustic panels</p></article><div class="afd-specs"><div class="afd-specs__item">
            Architects: Example Studio</div></div><ul id="gallery-thumbs"><li><img src="{AD_IMG}"></li></ul>
            <img src="https://images.adsttc.com/media/images/advert.jpg">'''), AD, 'acoustic')
        self.assertEqual(doc['image_count'], 1)
        self.assertTrue(doc['focus_match_found'])
        self.assertIsNone(doc['photography_credit'])

    def test_archdaily_article_not_misreported_as_project(self):
        with self.assertRaises(ValueError):
            sources.parse_archdaily(soup('<h1>Subscription</h1><article id="single-content"></article>'), AD, '')

    def test_frame_only_selected_project_and_location(self):
        content = {'type': 'project', 'status': 1, 'title': 'Test', 'url': '/project/example-office',
                   'company': 'Studio', 'city': 'Designer city', 'completion': '2024',
                   'published_at': '2025', 'classification': 'shortlisted',
                   'location': {'location_formatted_address': 'Project city'},
                   'description': '<p>Acoustic ceiling</p>',
                   'images': [{'url': FRAME_IMG, 'data': {'custom_properties': {'credits': 'Photographer'}}}],
                   'floorplan': [{'url': FRAME_IMG.replace('photo', 'plan'),
                                  'data': {'custom_properties': {'credits': 'Architect'}}}]}
        doc = sources.parse_frame(frame_page(content, latest={'images': [AD_IMG]}), FRAME, '')
        self.assertEqual(doc['image_count'], 2)
        self.assertEqual(doc['images'][1]['kind'], 'plan')
        self.assertEqual(doc['photography_credit'], ['Photographer'])
        self.assertEqual(doc['source_metadata']['location'], 'Project city')
        self.assertEqual(doc['source_metadata']['classification'], 'shortlisted')

    def test_frame_rejects_wrong_identity_restricted_or_missing_content(self):
        for content in [None, [], {'type': 'article', 'status': 1, 'title': 'A'},
                        {'type': 'project', 'status': 0, 'title': 'A'},
                        {'type': 'project', 'status': 1, 'title': 'A', 'url': '/project/other'}]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                sources.parse_frame(frame_page(content), FRAME, '')

    def test_award_requires_exact_entry_no_gallery_bleed(self):
        for entry_id in [None, 'missing']:
            with self.assertRaises(ValueError):
                sources.parse_award_entry(award_page(), AWARDS, 'architizer', entry_id, '')
        doc = sources.parse_award_entry(award_page(), AWARDS, 'architizer', 'award.2', '')
        self.assertEqual(doc['architect'], 'Other Studio')
        self.assertEqual(doc['source_metadata']['award_status'], 'Finalist')
        self.assertEqual(doc['image_count'], 1)
        self.assertNotEqual(doc['images'][0]['url'], AWARD_IMG)

    def test_waf_is_award_board_not_photo(self):
        page = soup(f'''<div class="image-panel-content"><a href="https://www.worldbuildingsdirectory.com/entries/test/">
          <div class="panel-image" title="Workplace Winner 2025" style="background-image:url({WAF_IMG})"></div></a>
          <div class="image-panel-description"><p>Test Project by Test Studio</p></div></div>''')
        doc = sources.parse_award_entry(page, sources.WAF_AWARDS[2025], 'waf', 'test::Workplace Winner 2025', '')
        self.assertEqual(doc['images'][0]['kind'], 'award_board')
        self.assertEqual(doc['architect'], 'Test Studio')

    def test_waf_multiple_awards_keep_separate_entry_ids(self):
        panels = ''.join(f'''<div class="image-panel-content"><a href="https://www.worldbuildingsdirectory.com/entries/test/">
            <div class="panel-image" title="{award}" style="background-image:url({WAF_IMG})"></div></a>
            <div class="image-panel-description"><p>Same Project by Studio</p></div></div>'''
                         for award in ['Office Winner', 'Special Prize'])
        entries = sources.waf_entries(soup(panels), sources.WAF_AWARDS[2025])
        self.assertEqual(len({e['entry_id'] for e in entries}), 2)
        for entry in entries:
            doc = sources.parse_award_entry(soup(panels), sources.WAF_AWARDS[2025], 'waf', entry['entry_id'], '')
            self.assertEqual(doc['source_metadata']['award_status'], entry['award_status'])

    def test_journal_excludes_related_cards_and_does_not_invent_architect(self):
        page = soup('''<article class="l-article"><h1>Collection</h1><div class="c-post-content">
          <p>Several offices</p><img data-src="https://blog.architizer.com/wp-content/uploads/test.jpg"></div>
          <div class="related"><img src="https://blog.architizer.com/wp-content/uploads/unrelated.jpg"></div></article>''')
        doc = sources.parse_journal(page, 'https://architizer.com/blog/collection/', '')
        self.assertEqual(doc['record_type'], 'editorial_article')
        self.assertIsNone(doc['architect'])
        self.assertEqual(doc['image_count'], 1)

    def test_catalog_years_are_explicit(self):
        self.assertEqual(sources.catalog_url('architizer', 2013), 'https://winners.architizer.com/2013/')
        for source, year in [('archdaily', 2025), ('waf', 2024), ('architizer', 1900)]:
            with self.assertRaises(ValueError):
                sources.catalog_url(source, year)


class SourceIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_total_deadline_is_a_reported_network_failure(self):
        with patch('server._fetch_bytes', new=AsyncMock(side_effect=TimeoutError)):
            result = await server.read_reference(AD)
        self.assertEqual(result['status'], 'source_unavailable')
        self.assertIn('45-second', result['error'])

    async def test_catalog_filter_pagination_and_read_share_entry_id(self):
        with patch('server.html_page', new=AsyncMock(return_value=(award_page(), AWARDS, False))):
            page = await server.browse_reference_catalog('architizer', query='office', limit=1)
            self.assertEqual(page['matching_count'], 2)
            self.assertEqual(page['next_offset'], 1)
            doc = await server.read_reference(page['results'][0]['url'], entry_id=page['results'][0]['entry_id'])
            self.assertEqual(doc['title'], 'Test Office')
            empty = await server.browse_reference_catalog('architizer', query='missing')
            self.assertEqual(empty['status'], 'no_matches_in_catalog_slice')

    async def test_discovery_is_explicit_and_does_not_fetch(self):
        with patch('server.html_page', new=AsyncMock()) as fetch:
            for source in ['archdaily', 'frame', 'officesnapshots', 'dezeen']:
                result = await server.search_references('office ceiling', source=source)
                self.assertEqual(result['status'], 'host_search_required')
                self.assertEqual(result['results'], [])
            fetch.assert_not_called()

    async def test_all_preserves_partial_failures_and_source_diversity(self):
        async def search(source, *args):
            if source == 'officenext':
                return {'status': 'source_unavailable', 'error': 'Timeout', 'results': []}
            if source == 'architizer':
                return {'status': 'ok', 'results': [{'url': 'https://example.test/a', 'title': 'A'},
                                                    {'url': 'https://example.test/b', 'title': 'B'}]}
            if source == 'waf':
                return {'status': 'ok', 'results': [{'url': sources.WAF_AWARDS[2025], 'entry_id': 'a', 'title': 'W'}]}
            return {'status': 'host_search_required', 'results': []}
        with patch('server._search_source', new=search):
            result = await server.search_references('office', source='all', limit=2)
        self.assertEqual([r['title'] for r in result['results']], ['A', 'W'])
        self.assertEqual(result['source_reports'][0]['status'], 'source_unavailable')
        self.assertEqual(len(result['source_reports']), 8)

    async def test_journal_pagination_not_silently_ignored(self):
        result = await server.search_references('office', source='architizer', page=2)
        self.assertEqual(result['status'], 'unsupported_page')

    async def test_cross_provider_redirect_rejected_before_second_request(self):
        visited = []
        def handler(request):
            visited.append(str(request.url))
            return httpx.Response(302, headers={'location': FRAME})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('server.httpx.AsyncClient', return_value=client):
            with self.assertRaisesRegex(ValueError, 'Cross-source'):
                await server.fetch_bytes(AD, 'project')
        self.assertEqual(visited, [AD])

    async def test_cache_preserves_final_identity(self):
        server.CACHE.clear()
        actual = AD + '/'
        with patch('server.fetch_bytes', new=AsyncMock(return_value=(b'<h1>Test</h1>', 'text/html', actual))):
            first = await server.html_page(AD, 'project')
            second = await server.html_page(AD, 'project')
        self.assertEqual(first[1], second[1])
        self.assertTrue(second[2])
        server.CACHE.clear()


if __name__ == '__main__':
    unittest.main()
