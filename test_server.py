import asyncio, json, sys, unittest
from pathlib import Path
from bs4 import BeautifulSoup
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from server import parse_project, parse_search, safe_url
ROOT=Path(__file__).parent

class ParserChecks(unittest.TestCase):
    def test_reject_non_source_urls(self):
        for url in ["http://www.officenext.ru/projects/project-1-test/",
                    "https://officenext.ru.evil.test/projects/project-1-test/",
                    "https://localhost/projects/project-1-test/",
                    "https://user:pass@www.officenext.ru/projects/project-1-test/",
                    "file:///tmp/secret"]:
            with self.assertRaises(ValueError): safe_url(url,"project")
    def test_exact_project_boundary(self):
        with self.assertRaises(ValueError): safe_url("https://www.officenext.ru/projects/","project")
        with self.assertRaises(ValueError): safe_url("https://www.officenext.ru/account/","project")
        self.assertIn("www.officenext.ru",safe_url("https://officenext.ru/projects/project-123-abc/","project"))
    def test_search_excludes_navigation_duplicates(self):
        s=BeautifulSoup('<a href="/projects/">Projects</a><div class="preview-text"><h3><a href="/projects/project-1-one/">One</a></h3><p class="preview-text__desc-text">Lobby</p></div><h3><a href="/projects/project-1-one/">One</a></h3>','html.parser')
        r=parse_search(s,10)
        self.assertEqual(len(r),1);self.assertEqual(r[0]["description"],"Lobby")
    def test_gallery_excludes_ads_and_preserves_unknown(self):
        s=BeautifulSoup('<h1>Example</h1><article class="article"><p>Acoustic ceiling.</p></article><div class="gallery-wrap"><div class="gallery"><a href="/upload/a.jpg"><img src="/upload/thumb.jpg"></a><a href="/upload/a.jpg"></a></div></div><img src="/upload/advert.jpg">','html.parser')
        r=parse_project(s,"https://www.officenext.ru/projects/project-1-one/","ceiling")
        self.assertEqual(len(r["images"]),1)
        self.assertIsNone(r["architect"]);self.assertTrue(r["focus_match_found"])
        self.assertFalse(r["images"][0]["visual_reviewed"])
    def test_markup_change_not_fabricated(self):
        with self.assertRaises(ValueError):parse_project(BeautifulSoup("<h1>Login</h1>","html.parser"),"https://www.officenext.ru/projects/project-1-one/")
    def test_gallery_without_optional_wrapper(self):
        s=BeautifulSoup('<h1>Example</h1><article class="article"><p>Lobby</p></article><div class="gallery"><a href="/upload/a.jpg"></a></div>','html.parser')
        r=parse_project(s,"https://www.officenext.ru/projects/project-1-one/")
        self.assertEqual(r["image_count"],1)
        self.assertIsNone(r["images"][0]["section_context"])
    def test_image_route_and_redirect_targets(self):
        for url in ["https://www.officenext.ru/account/photo.jpg", "https://www.officenext.ru/upload/script.svg", "https://www.officenext.ru:8080/upload/a.jpg", "https://127.0.0.1/upload/a.jpg"]:
            with self.assertRaises(ValueError): safe_url(url,"image")
    def test_no_focus_match_is_not_evidence(self):
        s=BeautifulSoup('<h1>Example</h1><article class="article"><p>Reception desk.</p></article>','html.parser')
        r=parse_project(s,"https://www.officenext.ru/projects/project-1-one/","acoustic")
        self.assertFalse(r["focus_match_found"])
        self.assertEqual(r["source_excerpts"],["Reception desk."])

async def smoke():
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/"server.py")])
    report={"protocol":"stdio MCP","queries":[]}
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            init=await session.initialize()
            listed=await session.list_tools()
            report["tools"]=[t.name for t in listed.tools]
            assert len(report["tools"])==4
            for q in ["акустика","вестибюль","кабинет"]:
                response=await session.call_tool("search_references",{"query":q,"limit":3})
                assert not response.isError
                data=response.structuredContent or json.loads(response.content[0].text)
                assert data["status"]=="ok",data
                assert data["results"]
                first=data["results"][0]
                detail=await session.call_tool("read_reference",{"url":first["url"],"focus":q})
                doc=detail.structuredContent or json.loads(detail.content[0].text)
                assert doc["status"]=="ok",doc
                assert doc["image_count"]>0,doc
                report["queries"].append({"query":q,"results":len(data["results"]),"example":doc["title"],
                    "url":doc["url"],"architect":doc["architect"],"images":doc["image_count"],
                    "focus_match_found":doc["focus_match_found"]})
            photo=await session.call_tool("view_reference_image",{"project_url":report["queries"][0]["url"],"image_index":0})
            assert not photo.isError
            image=next(c for c in photo.content if c.type=="image")
            assert len(image.data)>1000 and image.mimeType=="image/jpeg"
            report["image_in_memory_verified"]=True
            bad=await session.call_tool("read_reference",{"url":"https://localhost/"})
            obj=bad.structuredContent or json.loads(bad.content[0].text)
            assert obj["status"]=="unsupported_or_changed_source"
            report["invalid_source_rejected"]=True
            return report

if __name__=="__main__":
    if "--live" in sys.argv:
        result=asyncio.run(smoke())
        print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        unittest.main()

