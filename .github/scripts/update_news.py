#!/usr/bin/env python3
"""Refresh the four daily news panels in the standalone trading-routine page."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
CATEGORIES = {
    "world": ('세계 주요 뉴스', 'world news'),
    "us": ('미국 경제 금융 시장 뉴스', 'US economy finance markets'),
    "korea": ('한국 경제 증시 뉴스', 'South Korea economy stock market'),
    "crypto": ('암호화폐 비트코인 이더리움 뉴스', 'cryptocurrency Bitcoin Ethereum'),
}
MAX_ITEMS = 5


def fetch_items(query, start_date, end_date):
    q = f'{query} after:{start_date.isoformat()} before:{end_date.isoformat()}'
    url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({
        'q': q, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'
    })
    request = urllib.request.Request(url, headers={'User-Agent': 'trading-routine-news-bot/1.0'})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    items = []
    for node in root.findall('./channel/item'):
        title = (node.findtext('title') or '').strip()
        link = (node.findtext('link') or '').strip()
        published = (node.findtext('pubDate') or '').strip()
        source_node = node.find('source')
        source = (source_node.text or '').strip() if source_node is not None else ''
        if not title or not link or not link.startswith('https://'):
            continue
        try:
            published_at = parsedate_to_datetime(published).astimezone(KST)
            if not (start_date <= published_at.date() < end_date):
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        items.append({'title': title, 'link': link, 'source': source, 'published': published_at})
        if len(items) >= MAX_ITEMS:
            break
    if len(items) < 3:
        raise RuntimeError(f'{query}: 전날 뉴스가 3건 미만입니다 ({len(items)}건)')
    return items


def render_panel(category, items):
    lis = []
    for item in items:
        title = escape(item['title'])
        link = escape(item['link'], quote=True)
        source = escape(item['source'])
        published = item['published'].strftime('%H:%M KST')
        label = f'{title} — {source}, {published}' if source else f'{title} — {published}'
        lis.append(f'<li><a href="{link}" target="_blank" rel="noopener noreferrer">{label}</a></li>')
    hidden = '' if category == 'world' else ' hidden'
    return (f'<div class="rt-news-panel" id="rt-panel-{category}" role="tabpanel" '
            f'aria-labelledby="rt-tab-{category}"{hidden}>'
            f'<ol class="rt-news-list">{"".join(lis)}</ol></div>')


def update_page(success, message, collected=None):
    source = PAGE.read_text(encoding='utf-8')
    match = re.search(r'data-srcdoc="(.*?)"></iframe>', source, re.DOTALL)
    if not match:
        raise RuntimeError('index.html에서 data-srcdoc 문서를 찾을 수 없습니다.')
    inner = unescape(match.group(1))
    now = datetime.now(KST)
    status_class = 'rt-news-status is-ok' if success else 'rt-news-status is-delayed'
    if success:
        status = (f'<div id="rt-news-status" class="{status_class}" role="status" aria-live="polite">'
                  f'<strong>정상 갱신</strong><span>전날 뉴스 · {now:%Y.%m.%d %H:%M} KST 기준</span></div>')
        for category, items in collected.items():
            pattern = rf'<div class="rt-news-panel" id="rt-panel-{category}"[^>]*>.*?</div>'
            inner, count = re.subn(pattern, lambda _: render_panel(category, items), inner, count=1, flags=re.DOTALL)
            if count != 1:
                raise RuntimeError(f'뉴스 패널을 찾을 수 없습니다: {category}')
        date_pattern = r'<small class="rt-news-date">.*?</small>'
        date_label = f'<small class="rt-news-date">{now:%Y.%m.%d} 뉴스 · {now:%Y.%m.%d %H:%M} KST 갱신 · 문장을 누르면 출처 확인</small>'
        inner, count = re.subn(date_pattern, lambda _: date_label, inner, count=1, flags=re.DOTALL)
        if count != 1:
            raise RuntimeError('뉴스 날짜 표시를 찾을 수 없습니다.')
    else:
        old_date = re.search(r'<small class="rt-news-date">(.*?)</small>', inner, re.DOTALL)
        last_success = unescape(old_date.group(1)) if old_date else '확인 가능한 기록 없음'
        status = (f'<div id="rt-news-status" class="{status_class}" role="status" aria-live="polite">'
                  f'<strong>뉴스 갱신 지연</strong><span>{escape(message)} · 기존 뉴스 유지</span>'
                  f'<small>마지막 성공 갱신: {last_success}</small></div>')
    status_pattern = r'<div id="rt-news-status" class="rt-news-status[^"]*"[^>]*>.*?</div>'
    if re.search(status_pattern, inner, re.DOTALL):
        inner = re.sub(status_pattern, lambda _: status, inner, count=1, flags=re.DOTALL)
    else:
        anchor = '<div class="rt-tabs" role="tablist"'
        if anchor not in inner:
            raise RuntimeError('뉴스 탭 위치를 찾을 수 없습니다.')
        inner = inner.replace(anchor, status + '\n    ' + anchor, 1)
    css = """
    .rt-news-status { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:0 0 12px; padding:10px 12px; border:1px solid var(--rt-border, #64748b); border-radius:10px; font-size:12px; }
    .rt-news-status strong { font-size:12px; }
    .rt-news-status span, .rt-news-status small { color:var(--rt-muted, #64748b); }
    .rt-news-status.is-ok { border-color:#16803d; }
    .rt-news-status.is-ok strong { color:#16803d; }
    .rt-news-status.is-delayed { border-color:#b45309; background:color-mix(in srgb, #f59e0b 9%, transparent); }
    .rt-news-status.is-delayed strong { color:#b45309; }
    """
    if ".rt-news-status {" not in inner:
        inner = inner.replace("</style>", css + "\n</style>", 1)
    escaped_inner = escape(inner, quote=True)
    source = source[:match.start(1)] + escaped_inner + source[match.end(1):]
    PAGE.write_text(source, encoding='utf-8')


def main():
    now = datetime.now(KST)
    yesterday = now.date() - timedelta(days=1)
    today = now.date()
    try:
        collected = {}
        for category, (label, query) in CATEGORIES.items():
            print(f'Collecting {label}: {yesterday} KST')
            collected[category] = fetch_items(query, yesterday, today)
        update_page(True, 'success', collected)
        print('News refreshed successfully.')
    except Exception as exc:
        message = f'자동 수집 실패: {str(exc)[:180]}'
        print(message, file=sys.stderr)
        update_page(False, message)
        # Keep the workflow green after recording the visible delay state.
        # The old news stays in place, so users never see a half-updated page.
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
