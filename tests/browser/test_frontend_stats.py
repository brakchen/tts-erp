"""Compiled/native stats chart and dev StrictMode preview; canned GET only."""
from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fastapi import Request
from render_support import overflow_report

from tests.browser import conftest as browser_conftest
from tts_erp_v2.api.v2 import frontend_pages

pytestmark = [pytest.mark.domain_browser, pytest.mark.requires_browser]
ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = {
    "total_requests": 70, "whitelisted_requests": 45, "today_requests": 4, "error_requests": 2,
    "by_host": [{"label": "TEST_very-long-domain-" * 5 + ".example", "count": 70, "percentage": "91.3"}],
    "by_method": [{"label": "GET", "count": 70, "percentage": "91.3"}],
    "by_status": [{"label": "未知", "count": 70, "percentage": "91.3"}],
    "daily": [{"date": f"2026-10-{day}", "count": count} for day, count in [(21, 12), (22, 40), (23, 0), (24, 18)]],
}


def _documents(directory: Path) -> None:
    # Content preview deliberately does not authorize/activate a backend page route.
    for prefix in ("", "/tts"):
        request = Request({"type": "http", "root_path": prefix, "path": "/v2/pages/intercept-stats", "headers": []})
        response = frontend_pages.render_frontend_page(request, "intercept-stats")
        (directory / f"{'tts-' if prefix else ''}intercept-stats.html").write_bytes(bytes(response.body))


@pytest.fixture()
def stats_renderer(frontend_build, tmp_path):
    _documents(tmp_path)
    with browser_conftest._serve_pages(tmp_path) as url:
        yield from browser_conftest._browser_renderer(url)


@pytest.mark.parametrize("prefix", ["", "/tts"])
@pytest.mark.parametrize("width", [375, 1440])
def test_compiled_stats_native_chart_and_table(stats_renderer, prefix, width):
    page = stats_renderer.open(f"{prefix}/v2/pages/intercept-stats", width=width)
    requests = []
    payload = [PAYLOAD]
    page.route("**/v2/intercept/requests/stats?*", lambda route: (
        requests.append(route.request.method), route.fulfill(json=payload[0])
    ))
    page.reload(wait_until="networkidle")
    page.get_by_role("grid", name="每日请求（UTC）").get_by_text("2026-10-23", exact=True).wait_for()
    page.locator(".uplot canvas").wait_for()
    assert page.locator(".uplot").count() == 1
    assert page.locator("canvas").evaluate("canvas => { const p=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data; for(let i=0;i<p.length;i+=4) if(p[i]===37&&p[i+1]===99&&p[i+2]===235&&p[i+3]>0) return true; return false; }")
    widths = page.locator("canvas").evaluate("""canvas => {
      const {width,height}=canvas,p=canvas.getContext('2d').getImageData(0,0,width,height).data,groups=[];
      let start=null;
      for(let x=0;x<=width;x++) {
        let blue=false;
        if(x<width) for(let y=0;y<height;y++) {const i=(y*width+x)*4;if(p[i]===37&&p[i+1]===99&&p[i+2]===235&&p[i+3]>0){blue=true;break;}}
        if(blue&&start===null)start=x;
        if(!blue&&start!==null){groups.push(x-start);start=null;}
      }
      return groups;
    }""")
    assert len(widths) == 3 and max(widths) - min(widths) <= 2, widths
    assert requests == ["GET"]
    refresh = page.get_by_role("button", name="刷新统计")
    refresh.focus()
    page.wait_for_function("el => document.activeElement === el", arg=refresh.element_handle())
    with page.expect_request(lambda request: request.url.endswith('/v2/intercept/requests/stats?days=7')):
        page.keyboard.press("Enter")
    page.locator(".uplot canvas").wait_for()
    grid = page.get_by_role("grid", name="每日请求（UTC）")
    grid.focus()
    page.keyboard.press("ArrowDown")
    assert grid.evaluate("el => el === document.activeElement || el.contains(document.activeElement)")
    report = overflow_report(page)
    assert report["scrollWidth"] <= report["clientWidth"] + 1, report
    page.set_viewport_size({"width": 1440 if width == 375 else 375, "height": 900})
    page.wait_for_function("() => { const c=document.querySelector('canvas'); const host=c?.closest('.uplot').parentElement; return c && host && Math.abs(c.getBoundingClientRect().width-host.getBoundingClientRect().width)<=1 && c.getBoundingClientRect().width<=document.documentElement.clientWidth; }")
    assert page.locator(".uplot").count() == 1
    payload[0] = {**PAYLOAD, "daily": []}
    page.get_by_role("button", name="刷新统计").click()
    page.get_by_text("暂无每日数据", exact=True).wait_for()
    assert page.locator(".uplot canvas").count() == 0
    payload[0] = PAYLOAD
    page.get_by_role("button", name="刷新统计").click()
    page.locator(".uplot canvas").wait_for()
    assert page.locator(".uplot").count() == 1 and requests == ["GET", "GET", "GET", "GET"]
    page.set_viewport_size({"width": width, "height": 1200})
    page.wait_for_function("() => { const c=document.querySelector('canvas'); return c && Math.abs(c.getBoundingClientRect().width-c.closest('.uplot').parentElement.getBoundingClientRect().width)<=1; }")
    artifacts = ROOT / "frontend/.cache/screenshots"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"TEST_intercept-stats_{width}_{prefix.strip('/') or 'direct'}.png"), full_page=True)
    assert all("bootstrap.min" not in url for url in page.evaluate("performance.getEntriesByType('resource').map(x=>x.name)"))
    assert "intercept-stats" in frontend_pages.MIGRATED_PAGE_IDS


def test_dev_upstream_wrapper_strictmode_update_destroy(frontend_browser_renderer, tmp_path, monkeypatch):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with (tmp_path / "TEST_stats_vite.log").open("w") as log:
        process = subprocess.Popen(["npm", "run", "dev", "--", "--port", str(port)], cwd=ROOT / "frontend",
                                   env={**os.environ, "NODE_ENV": "development"}, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                assert process.poll() is None
                try:
                    with urllib.request.urlopen(origin + "/@vite/client", timeout=1) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                pytest.fail("local Vite did not become ready")
            monkeypatch.setenv("TTS_ERP_FRONTEND_DEV_ORIGIN", origin)
            _documents(tmp_path)
            with browser_conftest._serve_pages(tmp_path) as backend:
                page = frontend_browser_renderer.open("/v2/pages/dashboard")
                page.route("**/v2/intercept/requests/stats?*", lambda route: route.fulfill(json=PAYLOAD))
                page.goto(backend + "/tts/v2/pages/intercept-stats", wait_until="networkidle")
                page.locator(".uplot canvas").wait_for(timeout=20_000)
                result = page.evaluate("""async () => {
                  const urls=performance.getEntriesByType('resource').map(x=>x.name);
                  const find=name => { const u=urls.find(u=>new URL(u).pathname.endsWith('/'+name+'.js')); if(!u)throw Error('Missing actual Vite module '+name); return u; };
                  const reactModule=await import(find('react'));const React=reactModule.default??reactModule;
                  const domModule=await import(find('react-dom_client'));const createRoot=domModule.createRoot??domModule.default.createRoot;
                  const {default:Wrapper}=await import(find('uplot-react'));
                  const host=document.createElement('div');document.body.append(host);
                  const root=createRoot(host); const plots=[]; let destroys=0;
                  const onCreate=plot=>{ plots.push(plot); const original=plot.destroy;plot.destroy=()=>{destroys++;original();}; };
                  const settle=()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
                  const draw=(width,data)=>root.render(React.createElement(React.StrictMode,null,React.createElement(Wrapper,{options:{width,height:160,series:[{},{}]},data,onCreate})));
                  draw(240,[[1,2],[12,40]]);
                  for(let i=0;i<60&&plots.length<2;i++)await settle();
                  const strictCreates=plots.length,strictDestroys=destroys;
                  draw(300,[[1,2],[18,0]]);
                  for(let i=0;i<60&&!(plots.at(-1)?.data[1][0]===18&&plots.at(-1)?.width===300);i++)await settle();
                  const current=plots.at(-1);const updated=current.data[1][0]===18&&current.width===300&&host.querySelectorAll('canvas').length===1;
                  root.unmount();await settle();const allDestroyed=destroys===plots.length&&host.querySelectorAll('canvas').length===0;host.remove();
                  return {strictCreates,strictDestroys,updated,allDestroyed};
                }""")
                assert result["strictCreates"] >= 2 and result["strictDestroys"] >= 1, result
                assert result["updated"] and result["allDestroyed"], result
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
