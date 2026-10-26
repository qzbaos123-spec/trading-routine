#!/usr/bin/env python3
"""Refresh the four daily news panels in the standalone trading-routine page."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
import json
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "index.html"
KST = timezone(timedelta(hours=9))
RSS_LOCALE = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"}
CATEGORIES = {
    "world": ("세계 주요 뉴스", ["세계", "국제 뉴스", "글로벌 뉴스"]),
    "us": ("미국 경제 금융 시장 뉴스", ["미국", "미국 경제", "미국 주식"]),
    "korea": ("한국 경제 증시 뉴스", ["한국 경제", "코스피", "한국 증시"]),
    "crypto": ("암호화폐 비트코인 이더리움 뉴스", ["비트코인", "가상자산", "이더리움"]),
}
MAX_ITEMS = 5
_TRANSLATION_CACHE = {}
HANGUL_RE = re.compile(r"[가-힣]")


def translate_title_to_korean(title):
    """Translate English/foreign headlines only; never generate a summary."""
    if HANGUL_RE.search(title):
        return title
    if title in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[title]

    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": title
    })
    last_error = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 trading-routine-news-bot/1.0"}
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                translated_data = json.loads(response.read().decode("utf-8"))
            translated = "".join(
                part[0] for part in translated_data[0]
                if isinstance(part, list) and part and isinstance(part[0], str)
            ).strip()
            if not translated or not HANGUL_RE.search(translated):
                raise RuntimeError("번역 결과가 한국어 제목으로 확인되지 않았습니다.")
            _TRANSLATION_CACHE[title] = translated
            return translated
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"영문 제목 한국어 번역 실패: {str(last_error)[:120]}")



def translate_text_to_korean(text):
    """Translate verified source text to Korean; do not invent or expand it."""
    if not text.strip():
        return ""
    # Avoid unnecessary external translation calls for text already in Korean.
    hangul = len(HANGUL_RE.findall(text))
    letters = sum(1 for char in text if char.isalpha())
    if hangul and letters and hangul / letters > 0.15:
        return text.strip()
    url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode({
        "client": "gtx", "sl": "auto", "tl": "ko", "dt": "t", "q": text
    })
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 trading-routine-news-bot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        translated_data = json.loads(response.read().decode("utf-8"))
    translated = "".join(
        part[0] for part in translated_data[0]
        if isinstance(part, list) and part and isinstance(part[0], str)
    ).strip()
    if not translated or not HANGUL_RE.search(translated):
        raise RuntimeError("한국어 번역 결과를 확인할 수 없습니다.")
    return translated


class ArticleTextParser(HTMLParser):
    """Extract only paragraphs inside article/main and JSON-LD articleBody."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.content_depth = 0
        self.paragraph_depth = 0
        self.paragraph_parts = []
        self.paragraphs = []
        self.all_paragraphs = []
        self.jsonld_active = False
        self.jsonld_parts = []
        self.jsonld_blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("article", "main"):
            self.content_depth += 1
        if tag == "p":
            if self.paragraph_depth == 0:
                self.paragraph_parts = []
            self.paragraph_depth += 1
        if tag == "script" and "ld+json" in attrs.get("type", "").lower():
            self.jsonld_active = True
            self.jsonld_parts = []

    def handle_endtag(self, tag):
        if tag == "p" and self.paragraph_depth > 0:
            self.paragraph_depth -= 1
            if self.paragraph_depth == 0:
                paragraph = " ".join(" ".join(self.paragraph_parts).split())
                if paragraph:
                    self.all_paragraphs.append(paragraph)
                    if self.content_depth > 0:
                        self.paragraphs.append(paragraph)
                self.paragraph_parts = []
        if tag in ("article", "main") and self.content_depth > 0:
            self.content_depth -= 1
        if tag == "script" and self.jsonld_active:
            self.jsonld_blocks.append("".join(self.jsonld_parts))
            self.jsonld_active = False
            self.jsonld_parts = []

    def handle_data(self, data):
        if self.jsonld_active:
            self.jsonld_parts.append(data)
        elif self.paragraph_depth > 0:
            self.paragraph_parts.append(data)


def find_article_body(value):
    if isinstance(value, dict):
        body = value.get("articleBody")
        if isinstance(body, str) and len(body.strip()) >= 80:
            return body.strip()
        for child in value.values():
            found = find_article_body(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_article_body(child)
            if found:
                return found
    return ""


def extract_source_body(html_text):
    parser = ArticleTextParser()
    parser.feed(html_text)
    for block in parser.jsonld_blocks:
        try:
            body = find_article_body(json.loads(block))
            if body:
                return body
        except (ValueError, TypeError):
            continue
    # Never summarize generic page paragraphs: they may be recommendations or ads.
    # Only structured articleBody or paragraphs inside semantic article/main tags count.
    paragraphs = [p for p in parser.paragraphs if len(p) >= 45]
    body = " ".join(paragraphs[:8]).strip()
    return body if len(body) >= 100 else ""


def summarize_verified_body(body):
    """Return up to two lead sentences actually present in the source article."""
    normalized = " ".join(body.split())
    sentences = re.split(r"(?<=[.!?。！？])\s+", normalized)
    selected = [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 25][:2]
    if not selected:
        return ""
    return " ".join(selected)



def resolve_google_news_url(link):
    """Resolve Google's opaque RSS redirect through its article-page RPC metadata."""
    if "news.google.com" not in urllib.parse.urlparse(link).netloc:
        return link
    try:
        request = urllib.request.Request(
            link, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(request, timeout=6) as response:
            page = response.read(1_500_000).decode(
                response.headers.get_content_charset() or "utf-8", errors="replace"
            )
        match = re.search(r'<c-wiz[^>]*\bdata-p="([^"]+)"', page)
        if not match:
            print(f"Google News URL unresolved: no data-p signature ({link})")
            return link
        data_p = unescape(match.group(1))
        request_data = json.loads(data_p.replace("%.@.", '["garturlreq",'))
        rpc_payload = json.dumps([[[
            "Fbv4je", json.dumps(request_data[:-6] + request_data[-2:]), "null", "generic"
        ]]])
        post_body = urllib.parse.urlencode({"f.req": rpc_payload}).encode("utf-8")
        post_request = urllib.request.Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data=post_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            },
            method="POST",
        )
        with urllib.request.urlopen(post_request, timeout=6) as response:
            payload = response.read().decode("utf-8", errors="replace")
        payload = payload.removeprefix(")]}'")
        result = json.loads(payload)
        decoded = json.loads(result[0][2])
        candidate = decoded[1]
        if isinstance(candidate, str) and candidate.startswith("https://") and "news.google.com" not in urllib.parse.urlparse(candidate).netloc:
            return candidate
    except Exception as exc:
        print(f"Google News URL unresolved ({link}): {str(exc)[:100]}")
    return link

def fetch_article_summary(link):
    """Resolve and read the publisher page; never infer unavailable content."""
    resolved_link = resolve_google_news_url(link)
    if "news.google.com" in urllib.parse.urlparse(resolved_link).netloc:
        return "본문 확인 불가", resolved_link
    try:
        request = urllib.request.Request(
            resolved_link, headers={"User-Agent": "Mozilla/5.0 (compatible; trading-routine-news-bot/1.0)"}
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            resolved_link = response.geturl() or resolved_link
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                print(f"Article body unavailable: non-text content ({resolved_link}, {content_type})")
                return "본문 확인 불가", resolved_link
            raw = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            html_text = raw.decode(charset, errors="replace")
        body = extract_source_body(html_text)
        if not body:
            parser = ArticleTextParser()
            parser.feed(html_text)
            print(f"Article body unavailable: no readable article text ({resolved_link}; article paragraphs={len(parser.paragraphs)}, page paragraphs={len(parser.all_paragraphs)})")
            return "본문 확인 불가", resolved_link
        excerpt = summarize_verified_body(body)
        if not excerpt:
            print(f"Article body unavailable: no extractable sentences ({resolved_link})")
            return "본문 확인 불가", resolved_link
        try:
            return translate_text_to_korean(excerpt), resolved_link
        except Exception:
            return "한국어 번역 실패", resolved_link
    except Exception as exc:
        print(f"Article body unavailable ({resolved_link}): {str(exc)[:100]}")
        return "본문 확인 불가", resolved_link

def fetch_items(queries, start_date, end_date):
    """Combine several Korean-locale searches to avoid sparse category feeds."""
    by_link = {}
    for query in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query, **RSS_LOCALE
        })
        request = urllib.request.Request(
            url, headers={"User-Agent": "trading-routine-news-bot/1.0"}
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = response.read()
        root = ET.fromstring(payload)
        for node in root.findall("./channel/item"):
            original_title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = (node.findtext("pubDate") or "").strip()
            source_node = node.find("source")
            source = (source_node.text or "").strip() if source_node is not None else ""
            if not original_title or not link or not link.startswith("https://") or link in by_link:
                continue
            try:
                published_at = parsedate_to_datetime(published).astimezone(KST)
                if not (start_date <= published_at.date() < end_date):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            by_link[link] = {"original_title": original_title, "link": link,
                             "source": source, "published": published_at}

    # Select recent items, translate headlines, and summarize only source text we can read.
    raw_items = sorted(by_link.values(), key=lambda item: item["published"], reverse=True)[:MAX_ITEMS]
    if not raw_items:
        raise RuntimeError("전날 뉴스가 없습니다: " + " / ".join(queries))
    items = []
    for item in raw_items:
        item["title"] = translate_title_to_korean(item.pop("original_title"))
        items.append(item)
    # Resolve/read article pages concurrently to keep the scheduled workflow bounded.
    with ThreadPoolExecutor(max_workers=4) as pool:
        summaries = list(pool.map(lambda item: fetch_article_summary(item["link"]), items))
    for item, (summary, resolved_link) in zip(items, summaries):
        item["summary"] = summary
        if resolved_link.startswith("https://") and "news.google.com" not in urllib.parse.urlparse(resolved_link).netloc:
            item["link"] = resolved_link
    return items


def render_panel(category, items):
    lis = []
    for item in items:
        title = escape(item["title"])
        link = escape(item["link"], quote=True)
        source = escape(item["source"] or "출처 미상")
        published = item["published"].strftime("%H:%M KST")
        summary = escape(item.get("summary") or "본문 확인 불가")
        lis.append(
            f'<li class="rt-news-item">'
            f'<a class="rt-news-title" href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
            f'<p class="rt-news-meta">{source} · {published} · '
            f'<a href="{link}" target="_blank" rel="noopener noreferrer">원문 링크</a></p>'
            f'<p class="rt-news-summary">{summary}</p>'
            f'</li>'
        )
    hidden = "" if category == "world" else " hidden"
    return (f'<div class="rt-news-panel" id="rt-panel-{category}" role="tabpanel" '
            f'aria-labelledby="rt-tab-{category}"{hidden}>'
            f'<ol class="rt-news-list">{"".join(lis)}</ol></div>')


def update_page(success, message, collected=None):
    source = PAGE.read_text(encoding="utf-8")
    match = re.search(r'data-srcdoc="(.*?)"></iframe>', source, re.DOTALL)
    if not match:
        raise RuntimeError("index.html에서 data-srcdoc 문서를 찾을 수 없습니다.")
    inner = unescape(match.group(1))
    now = datetime.now(KST)
    status_class = "rt-news-status is-ok" if success else "rt-news-status is-delayed"
    if success:
        status = (f'<div id="rt-news-status" class="{status_class}" role="status" aria-live="polite">'
                  f'<strong>정상 갱신</strong><span>전날 뉴스 · {now:%Y.%m.%d %H:%M} KST 기준</span></div>')
        for category, items in collected.items():
            pattern = rf'<div class="rt-news-panel" id="rt-panel-{category}"[^>]*>.*?</div>'
            inner, count = re.subn(
                pattern, lambda _: render_panel(category, items), inner,
                count=1, flags=re.DOTALL
            )
            if count != 1:
                raise RuntimeError(f"뉴스 패널을 찾을 수 없습니다: {category}")
        date_pattern = r'<small class="rt-news-date">.*?</small>'
        news_day = now.date() - timedelta(days=1)
        date_label = (f'<small class="rt-news-date">{news_day:%Y.%m.%d} 뉴스 · '
                      f'{now:%Y.%m.%d %H:%M} KST 갱신 · 문장을 누르면 출처 확인</small>')
        inner, count = re.subn(date_pattern, lambda _: date_label, inner, count=1, flags=re.DOTALL)
        if count != 1:
            raise RuntimeError("뉴스 날짜 표시를 찾을 수 없습니다.")
    else:
        old_date = re.search(r'<small class="rt-news-date">(.*?)</small>', inner, re.DOTALL)
        last_success = unescape(old_date.group(1)) if old_date else "확인 가능한 기록 없음"
        status = (f'<div id="rt-news-status" class="{status_class}" role="status" aria-live="polite">'
                  f'<strong>뉴스 갱신 지연</strong><span>{escape(message)} · 기존 뉴스 유지</span>'
                  f'<small>마지막 성공 갱신: {last_success}</small></div>')
    status_pattern = r'<div id="rt-news-status" class="rt-news-status[^"]*"[^>]*>.*?</div>'
    if re.search(status_pattern, inner, re.DOTALL):
        inner = re.sub(status_pattern, lambda _: status, inner, count=1, flags=re.DOTALL)
    else:
        anchor = '<div class="rt-tabs" role="tablist"'
        if anchor not in inner:
            raise RuntimeError("뉴스 탭 위치를 찾을 수 없습니다.")
        inner = inner.replace(anchor, status + "\n    " + anchor, 1)
    css = """
    .rt-news-status { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; margin:0 0 12px; padding:10px 12px; border:1px solid var(--rt-border, #64748b); border-radius:10px; font-size:12px; }
    .rt-news-status strong { font-size:12px; }
    .rt-news-status span, .rt-news-status small { color:var(--rt-muted, #64748b); }
    .rt-news-status.is-ok { border-color:#16803d; }
    .rt-news-status.is-ok strong { color:#16803d; }
    .rt-news-status.is-delayed { border-color:#b45309; background:color-mix(in srgb, #f59e0b 9%, transparent); }
    .rt-news-status.is-delayed strong { color:#b45309; }
    """
    if ".rt-news-summary {" not in inner:
        news_css = """
    #trading-routine-dashboard .rt-news-item { padding:7px 0 9px; border-bottom:1px solid var(--rt-border); }
    #trading-routine-dashboard .rt-news-title { display:inline-block; font-weight:600; line-height:1.5; }
    #trading-routine-dashboard .rt-news-meta { margin:3px 0 5px; color:var(--rt-muted); font-size:11px; line-height:1.45; }
    #trading-routine-dashboard .rt-news-meta a { color:var(--rt-green); text-decoration:underline; }
    #trading-routine-dashboard .rt-news-summary { margin:0; color:var(--rt-text); font-size:12px; line-height:1.6; overflow-wrap:anywhere; }
    """
        inner = inner.replace("</style>", news_css + "\n</style>", 1)
    escaped_inner = escape(inner, quote=True)
    source = source[:match.start(1)] + escaped_inner + source[match.end(1):]
    PAGE.write_text(source, encoding="utf-8")


def main():
    now = datetime.now(KST)
    yesterday = now.date() - timedelta(days=1)
    today = now.date()
    try:
        collected = {}
        for category, (label, queries) in CATEGORIES.items():
            print(f"Collecting {label}: {yesterday} KST (hl=ko, gl=KR, ceid=KR:ko)")
            collected[category] = fetch_items(queries, yesterday, today)
        update_page(True, "success", collected)
        print("News refreshed successfully.")
    except Exception as exc:
        message = f"자동 수집 실패: {str(exc)[:180]}"
        print(message, file=sys.stderr)
        update_page(False, message)
        # Never publish untranslated English-only headlines as a successful refresh.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
