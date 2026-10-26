#!/usr/bin/env python3
"""Refresh the four daily news panels in the standalone trading-routine page."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from pathlib import Path
import json
import re
import difflib
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
# Each category uses market-focused queries and a strict headline relevance gate.
# The selector intentionally returns fewer than five stories when there is not enough signal.
CATEGORIES = {
    "world": (
        "세계 경제·금융 시장 뉴스",
        [
            '"global markets" OR "world economy" OR "financial markets" OR "central bank"',
            '"bond yields" OR inflation OR "interest rates" OR "oil prices" OR "trade policy"',
            '"corporate earnings" OR recession OR GDP OR "currency markets" OR tariffs',
            "세계 경제 금융시장 중앙은행 금리 물가 채권 환율 유가 무역 기업실적",
        ],
        [
            "global markets", "financial markets", "world economy", "global economy",
            "central bank", "interest rate", "inflation", "bond yield", "stock market",
            "equities", "corporate earnings", "trade policy", "tariff", "oil price",
            "crude oil", "currency", "forex", "recession", "gdp", "imf", "ecb",
            "bank of japan", "market selloff", "investor", "financial", "economy",
            "경제", "금리", "물가", "채권", "증시", "주식", "기업 실적", "중앙은행",
            "환율", "유가", "무역", "관세", "국채", "금융", "투자", "성장률",
        ],
    ),
    "us": (
        "미국 경제·금융 시장 뉴스",
        [
            '"US economy" OR "Wall Street" OR "Federal Reserve" OR "stock market"',
            '"S&P 500" OR Nasdaq OR "Treasury yields" OR "US inflation" OR "US jobs"',
            '"US corporate earnings" OR "SEC regulation" OR "US trade policy" OR tariffs',
            "미국 경제 연준 FOMC 뉴욕증시 미 국채 미국 기업 실적 달러 관세",
        ],
        [
            "us economy", "u.s. economy", "american economy", "wall street",
            "federal reserve", "fed", "fomc", "s&p 500", "nasdaq", "dow jones",
            "treasury yield", "us inflation", "u.s. inflation", "us jobs", "us gdp",
            "sec", "dollar", "us stocks", "american stocks", "corporate earnings",
            "quarterly results", "merger", "acquisition", "ipo", "tariff",
            "연준", "미국 경제", "미국 증시", "뉴욕증시", "미 국채", "미국 기업",
            "미국 물가", "미국 고용", "달러", "관세", "실적 발표",
        ],
    ),
    "korea": (
        "한국 경제·금융·기업 뉴스",
        [
            "한국 경제 증시 코스피 코스닥 환율 금리 물가 수출",
            "한국은행 기준금리 금융정책 국채 채권 기업 실적",
            "삼성전자 SK하이닉스 현대차 LG에너지솔루션 실적 수출 투자",
            '"Korea economy" OR KOSPI OR "Bank of Korea" OR "Korean won" OR exports',
        ],
        [
            "korea economy", "korean economy", "kospi", "kosdaq", "bank of korea",
            "korean won", "korea exports", "korean exports", "korea inflation",
            "korean stocks", "korea stocks", "samsung", "sk hynix", "hyundai motor",
            "lg energy", "naver", "kakao", "posco", "corporate earnings", "interest rate",
            "한국 경제", "한국 증시", "코스피", "코스닥", "한국은행", "원화", "환율",
            "수출", "수입", "무역수지", "물가", "기준금리", "금융정책", "국채",
            "기업 실적", "삼성전자", "하이닉스", "현대차", "네이버", "카카오",
            "반도체", "배터리", "주가", "증시", "투자",
        ],
    ),
    "crypto": (
        "코인·가상자산 시장 뉴스",
        [
            "비트코인 이더리움 가상자산 암호화폐 가격 시장",
            "crypto markets bitcoin ethereum regulation stablecoin exchange",
            '"bitcoin ETF" OR "ethereum ETF" OR "crypto regulation" OR stablecoin',
            "가상자산 거래소 ETF 스테이블코인 블록체인 규제",
        ],
        [
            "bitcoin", "btc", "ethereum", "ether", "crypto", "cryptocurrency",
            "digital asset", "tokenization", "tokenized", "stablecoin", "defi",
            "altcoin", "crypto exchange", "crypto regulation", "bitcoin etf",
            "ethereum etf", "blockchain finance", "비트코인", "이더리움", "가상자산",
            "암호화폐", "코인", "스테이블코인", "거래소", "토큰", "블록체인",
            "가상자산 규제", "코인 시장", "암호화폐 시장",
        ],
    ),
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
        self.article_depth = 0
        self.main_depth = 0
        self.headline_depth = 0
        self.headline_parts = []
        self.headlines = []
        self.seen_h1 = False
        self.paragraph_depth = 0
        self.paragraph_parts = []
        self.paragraphs = []
        self.article_paragraphs = []
        self.main_paragraphs_after_h1 = []
        self.all_paragraphs = []
        self.jsonld_active = False
        self.jsonld_parts = []
        self.jsonld_blocks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("article", "main"):
            self.content_depth += 1
        if tag == "article":
            self.article_depth += 1
        if tag == "main":
            self.main_depth += 1
        if tag == "h1":
            if self.headline_depth == 0:
                self.headline_parts = []
            self.headline_depth += 1
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
                    if self.article_depth > 0:
                        self.article_paragraphs.append(paragraph)
                    if self.main_depth > 0 and self.seen_h1:
                        self.main_paragraphs_after_h1.append(paragraph)
                self.paragraph_parts = []
        if tag == "h1" and self.headline_depth > 0:
            self.headline_depth -= 1
            if self.headline_depth == 0:
                headline = " ".join(" ".join(self.headline_parts).split())
                if headline:
                    self.headlines.append(headline)
                self.headline_parts = []
                self.seen_h1 = True
        if tag == "article" and self.article_depth > 0:
            self.article_depth -= 1
        if tag == "main" and self.main_depth > 0:
            self.main_depth -= 1
        if tag in ("article", "main") and self.content_depth > 0:
            self.content_depth -= 1
        if tag == "script" and self.jsonld_active:
            self.jsonld_blocks.append("".join(self.jsonld_parts))
            self.jsonld_active = False
            self.jsonld_parts = []

    def handle_data(self, data):
        if self.jsonld_active:
            self.jsonld_parts.append(data)
        elif self.headline_depth > 0:
            self.headline_parts.append(data)
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


def headline_matches(expected_title, source, candidates):
    expected = expected_title.strip()
    suffix = " - " + (source or "")
    if suffix.strip() and expected.endswith(suffix):
        expected = expected[:-len(suffix)]
    tokens = lambda value: set(re.findall(r"[A-Za-z0-9가-힣]+", value.lower()))
    expected_tokens = tokens(expected)
    if not expected_tokens:
        return False
    for headline in candidates:
        headline_tokens = tokens(headline)
        if not headline_tokens:
            continue
        overlap = len(expected_tokens & headline_tokens) / max(1, min(len(expected_tokens), len(headline_tokens)))
        similarity = difflib.SequenceMatcher(None, expected.lower(), headline.lower()).ratio()
        if overlap >= 0.35 or similarity >= 0.45:
            return True
    return False



def select_relevant_paragraphs(paragraphs, expected_title, source):
    """Prefer paragraphs containing the article headline's key terms."""
    title = expected_title.strip()
    suffix = " - " + (source or "")
    if suffix.strip() and title.endswith(suffix):
        title = title[:-len(suffix)]
    terms = set(re.findall(r"[A-Za-z0-9가-힣]+", title.lower()))
    stop_words = {
        "the", "and", "for", "with", "from", "that", "this", "into", "over",
        "after", "before", "about", "what", "when", "why", "how", "are", "was",
        "한국", "미국", "세계", "뉴스", "관련", "오늘", "전망", "분석", "보도",
    }
    terms = {term for term in terms if len(term) >= 2 and term not in stop_words}
    candidates = [p for p in paragraphs if len(p) >= 45]
    if not candidates or not terms:
        return candidates[:8]
    scored = [(sum(1 for term in terms if term in p.lower()), p) for p in candidates]
    best = max(score for score, _ in scored)
    required = 2 if len(terms) >= 4 else 1
    if best < required:
        return []
    threshold = max(required, (best + 1) // 2)
    return [p for score, p in scored if score >= threshold][:8]

def extract_source_body(html_text, expected_title="", source=""):
    parser = ArticleTextParser()
    parser.feed(html_text)
    for block in parser.jsonld_blocks:
        try:
            body = find_article_body(json.loads(block))
            if body:
                return body
        except (ValueError, TypeError):
            continue
    # Prefer paragraphs in the actual <article>; otherwise require paragraphs
    # after a matching H1 within <main>. This prevents related-story teasers
    # from being mistaken for the current article.
    article_paragraphs = [p for p in parser.article_paragraphs if len(p) >= 45]
    if article_paragraphs:
        if parser.headlines and expected_title and not headline_matches(expected_title, source, parser.headlines):
            return ""
        relevant = select_relevant_paragraphs(article_paragraphs, expected_title, source)
        body = " ".join(relevant).strip()
        if len(body) >= 100:
            return body
    main_paragraphs = [p for p in parser.main_paragraphs_after_h1 if len(p) >= 45]
    if parser.headlines and expected_title and headline_matches(expected_title, source, parser.headlines):
        relevant = select_relevant_paragraphs(main_paragraphs, expected_title, source)
        body = " ".join(relevant).strip()
        if len(body) >= 100:
            return body
    return ""


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

def fetch_article_summary(link, expected_title="", source=""):
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
        body = extract_source_body(html_text, expected_title, source)
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

def _keyword_matches(title, keyword):
    """Match English keywords as whole words and Korean phrases as substrings."""
    keyword = keyword.lower().strip()
    if not keyword:
        return False
    if HANGUL_RE.search(keyword):
        return keyword in title
    return re.search(r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])", title) is not None


def is_relevant_title(category, title):
    """Reject general-interest stories unless their headline signals market relevance."""
    normalized = re.sub(r"\s+", " ", title.lower()).strip()
    irrelevant = [
        "celebrity", "celebrities", "movie review", "film review", "box office",
        "music festival", "reality show", "football match", "soccer match",
        "basketball game", "sports results", "recipe", "restaurant review",
        "travel guide", "horoscope", "fashion week", "wedding", "school lunch",
        "crime scene", "murder investigation", "weather forecast", "traffic accident",
        "연예인", "연예계", "드라마", "영화 리뷰", "박스오피스", "음악 축제",
        "축구 경기", "야구 경기", "농구 경기", "스포츠 경기", "맛집", "여행지",
        "레시피", "운세", "패션쇼", "결혼식", "학교 급식", "날씨 예보",
        "교통사고", "살인 사건", "생활 꿀팁",
    ]
    if any(_keyword_matches(normalized, word) for word in irrelevant):
        return False
    keywords = CATEGORIES[category][2]
    return any(_keyword_matches(normalized, keyword) for keyword in keywords)


def fetch_items(category, queries, start_date, end_date):
    """Collect a broad candidate pool, filter by market relevance, then rank by time."""
    by_link = {}
    successful_feeds = 0
    for query in queries:
        url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
            "q": query, **RSS_LOCALE
        })
        request = urllib.request.Request(
            url, headers={"User-Agent": "trading-routine-news-bot/1.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = response.read()
            root = ET.fromstring(payload)
            successful_feeds += 1
        except Exception as exc:
            print(f"RSS query failed ({category}; {query}): {str(exc)[:120]}")
            continue
        for node in root.findall("./channel/item"):
            original_title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = (node.findtext("pubDate") or "").strip()
            source_node = node.find("source")
            source = (source_node.text or "").strip() if source_node is not None else ""
            if (not original_title or not link or not link.startswith("https://")
                    or link in by_link or not is_relevant_title(category, original_title)):
                continue
            try:
                published_at = parsedate_to_datetime(published).astimezone(KST)
                if not (start_date <= published_at.date() < end_date):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            by_link[link] = {"original_title": original_title, "link": link,
                             "source": source, "published": published_at}

    if successful_feeds == 0:
        raise RuntimeError(f"{category} RSS 검색이 모두 실패했습니다.")
    # Crucially, relevance filtering happens before selecting the latest five.
    raw_items = sorted(by_link.values(), key=lambda item: item["published"], reverse=True)[:MAX_ITEMS]
    if not raw_items:
        print(f"No market-relevant stories found for {category}; leaving category short.")
        return []
    items = []
    for item in raw_items:
        item["title"] = translate_title_to_korean(item["original_title"])
        items.append(item)
    # Resolve/read article pages concurrently; unreadable bodies stay explicitly labeled.
    with ThreadPoolExecutor(max_workers=4) as pool:
        summaries = list(pool.map(
            lambda item: fetch_article_summary(item["link"], item["original_title"], item["source"]),
            items
        ))
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
    if not lis:
        lis.append(
            '<li class="rt-news-empty" role="status">'
            '관련성 기준을 충족하는 경제·금융·기업·시장 정책 기사가 부족합니다. '
            '확인되지 않은 기사로 채우지 않았습니다.</li>'
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
    #trading-routine-dashboard .rt-news-empty { padding:14px 8px; color:var(--rt-muted); font-size:12px; line-height:1.6; list-style:none; }
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
        for category, (label, queries, _keywords) in CATEGORIES.items():
            print(f"Collecting {label}: {yesterday} KST (strict market relevance; hl=ko, gl=KR, ceid=KR:ko)")
            collected[category] = fetch_items(category, queries, yesterday, today)
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
