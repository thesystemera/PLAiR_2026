import asyncio
import time
import random
import threading
import datetime
from bs4 import BeautifulSoup
from gnews import GNews
from newspaper import Article, Config
from concurrent.futures import ThreadPoolExecutor
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from multiprocessing import cpu_count
from config import settings
from services import log_service

USER_AGENTS_FILE = getattr(settings, 'USER_AGENTS_FILE', 'data/user_agents.txt')

class NewsService:
    def __init__(self, ai_service):
        log_service.external("News Service Initialized")
        self.ai_service = ai_service
        self.thread_local = threading.local()
        self.USER_AGENTS = []
        self.GPT_NEWS_SCRAPING_MODEL = "llama3-70b-8192"
        self.GPT_NEWS_SCRAPING_TEMPERATURE = 0
        self.GPT_NEWS_SCRAPING_TOKENS = 250
        self.NUMBER_OF_WORKER_THREADS = cpu_count() * 2
        self.MAX_CHARACTERS = 1500
        self.CATEGORIES = ["World", "Nation", "Business", "Technology", "Entertainment", "Sports", "Science", "Health"]

    async def initialize(self):
        loop = asyncio.get_event_loop()
        self.USER_AGENTS = await loop.run_in_executor(None, self.load_user_agents)
        log_service.system("Service: News Service User Agents Loaded")

    @staticmethod
    def load_user_agents():
        with open(USER_AGENTS_FILE, 'r', encoding='utf-8') as file:
            return [line.strip().strip('",') for line in file if line.strip()]

    def get_random_user_agent(self):
        return random.choice(self.USER_AGENTS)

    def get_thread_browser(self):
        if not hasattr(self.thread_local, 'browser'):
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument(f"user-agent={self.get_random_user_agent()}")
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            self.thread_local.browser = webdriver.Chrome(options=chrome_options)
            self.thread_local.browser.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return self.thread_local.browser

    def close_thread_browser(self):
        if hasattr(self.thread_local, 'browser'):
            log_service.external("Closing thread browser")
            self.thread_local.browser.quit()
            del self.thread_local.browser

    def process_article(self, article_data, keyword):
        url = article_data['url']
        log_service.external(f"Processing article for URL: {url} with keyword: {keyword}")
        browser = None

        try:
            def validate_and_fix_url(url_to_fix):
                if not url_to_fix.startswith(('http://', 'https://')):
                    return f'https://{url_to_fix}'
                return url_to_fix

            def extract_url_from_google_news(gnews_url):
                try:
                    log_service.external(f"Extracting actual URL from Google News link: {gnews_url[:100]}")
                    browser_instance = self.get_thread_browser()
                    browser_instance.get(gnews_url)
                    WebDriverWait(browser_instance, 10).until(EC.presence_of_element_located((By.TAG_NAME, "a")))
                    links = browser_instance.find_elements(By.TAG_NAME, "a")
                    for link in links:
                        href = link.get_attribute("href")
                        if href and href.startswith('https://') and 'google' not in href:
                            return href
                    return None
                except Exception as inner_e:
                    log_service.external(f"Error extracting URL from Google News: {inner_e}")
                    return None

            if 'news.google.com' in url:
                extracted_url = extract_url_from_google_news(url)
                if extracted_url:
                    url = extracted_url
                else:
                    return None

            url = validate_and_fix_url(url)
            log_service.external(f"Fetching article from URL: {url[:100]}")

            config = Config()
            config.browser_user_agent = self.get_random_user_agent()
            article = Article(url, config=config)
            article.download()
            article.parse()
            article.nlp()

            title = article.title
            summary = article.summary
            full_text = article.text
            source_name = article.source_url
            date_published = article.publish_date or datetime.datetime.now(datetime.timezone.utc)

            if not title or not summary or not full_text:
                log_service.external(f"Re-fetching article details using browser for URL: {url[:100]}")
                browser = self.get_thread_browser()
                browser.get(url)
                time.sleep(random.uniform(2, 5))
                WebDriverWait(browser, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

                if not title:
                    title = browser.title
                if not full_text:
                    full_text = browser.find_element(By.TAG_NAME, "body").text
                if not summary:
                    soup = BeautifulSoup(browser.page_source, 'html.parser')
                    meta_summary = soup.find('meta', attrs={'name': 'description'})
                    summary = meta_summary['content'] if meta_summary else full_text[:500]

            date_published_formatted = date_published.strftime('%Y-%m-%dT%H:%M:%SZ') if isinstance(date_published,
                                                                                                   datetime.datetime) else date_published

            return {
                'source': {'id': None, 'name': source_name},
                'title': title,
                'description': summary,
                'url': url,
                'publishedAt': date_published_formatted,
                'content': full_text,
                'keyword': keyword
            }
        except Exception as e:
            log_service.external(f"Article processing failed for URL: {url[:100]}. Error: {e}")
            return None
        finally:
            if browser:
                self.close_thread_browser()

    async def process_rss_link_parallel(self, articles, keyword):
        loop = asyncio.get_event_loop()
        log_service.external(f"News: Processing {len(articles)} articles for keyword: {keyword} in parallel")
        with ThreadPoolExecutor(max_workers=self.NUMBER_OF_WORKER_THREADS) as executor:
            tasks = [
                loop.run_in_executor(executor, self.process_article, article, keyword)
                for article in articles
            ]
            processed_articles = await asyncio.gather(*tasks)
        return [article for article in processed_articles if article is not None]

    async def fetch_news(self, query, is_topic=False, language='en', country='US', period='7d', max_results=10):
        log_service.external(f"News: Fetching news for query: {query}")
        google_news = GNews(language=language, country=country, period=period, max_results=max_results)

        if is_topic:
            articles = google_news.get_news_by_topic(query.upper())
        else:
            articles = google_news.get_news(query)

        if articles is None:
            articles = []
        log_service.external(f"News: GNews returned {len(articles)} articles for query: {query}")
        if len(articles) > max_results:
            articles = articles[:max_results]

        return await self.process_rss_link_parallel(articles, query)

    async def assess_article_relevance_and_quality(self, article, query):
        log_service.external(f"News: Assessing relevance and quality for article: {article['title']}")
        relevance_prompt = f"""
As an experienced news curator, your task is to assess the relevance and quality of news articles to a given query or category.
Evaluate the article based on its headline, summary, source, and date published, considering factors such as:

1. Direct relevance to the query topic or selected category
2. Timeliness and currency of the information
3. Significance of the news in its field
4. Credibility of the source
5. Quality of writing and information presentation
6. Absence of bot-generated or low-quality content

Provide two scores:

1. Relevance score from 0 to 10, where:
   0 - Completely unrelated to the query or category
   10 - Highly relevant and directly addresses the query or fits perfectly in the category

2. Quality score from 0 to 10, where:
   0 - Clearly bot-generated or extremely low-quality content
   10 - High-quality content from a reputable source

Please respond with only the two numeric scores separated by a comma, without any additional explanation.

Query or Category: {query}
Headline: {article['title']}
Summary: {article['description'][:1000]}
Source: {article['source']['name']}
Date Published: {article['publishedAt']}
"""

        try:
            response = await self.ai_service.generate_generic(
                messages=[{'role': 'system', 'content': relevance_prompt}]
            )

            scores = response.choices[0].message.content.strip().split(',')
            if len(scores) != 2:
                log_service.external(f"News: Unexpected response format: {response.choices[0].message.content}")
                return 0.0, 0.0

            relevance_score = float(scores[0].strip())
            quality_score = float(scores[1].strip())

            log_service.external(f"News: Relevance Score: {relevance_score}, Quality Score: {quality_score} for article: {article['title']}")
            return relevance_score, quality_score
        except Exception as e:
            log_service.external(f"News: Error assessing article: {e}")
            return 0.0, 0.0

    async def fetch_and_analyze_news(self, query, is_topic=False, language='en', country='US', period='7d',
                                     relevance_threshold=5, quality_threshold=5, max_results=10):
        log_service.external(f"News: Fetching and analyzing news for query: {query}")
        articles = await self.fetch_news(query, is_topic, language, country, period, max_results=max_results)
        articles = [article for article in articles if article]

        tasks = [self.assess_article_relevance_and_quality(article, query) for article in articles]
        scores = await asyncio.gather(*tasks)

        analyzed_articles = [
            {**article, 'relevance_score': rel, 'quality_score': qual}
            for article, (rel, qual) in zip(articles, scores)
            if rel >= relevance_threshold and qual >= quality_threshold
        ]
        log_service.external(f"News: Analyzed {len(analyzed_articles)} articles after filtering by relevance and quality.")
        return sorted(analyzed_articles, key=lambda x: (x['relevance_score'], x['quality_score']), reverse=True)

    @staticmethod
    def generate_final_report(articles):
        report = "===== FINAL REPORT =====\n"
        report += f"Total articles processed: {len(articles)}\n\n"

        for i, article in enumerate(articles, 1):
            report += f"Rank {i}:\n"
            report += f"Title: <a href='{article['url']}'>{article['title']}</a>\n"
            report += f"Source: {article['source']['name']}\n"
            report += f"Date Published: {article['publishedAt']}\n"
            report += f"Relevance Score: {article['relevance_score']:.2f}\n"
            report += f"Quality Score: {article['quality_score']:.2f}\n"
            report += f"Summary: {article['description']}\n\n"

        if articles:
            avg_relevance = sum(article['relevance_score'] for article in articles) / len(articles)
            avg_quality = sum(article['quality_score'] for article in articles) / len(articles)
            report += f"Average Relevance Score: {avg_relevance:.2f}\n"
            report += f"Average Quality Score: {avg_quality:.2f}\n"
        else:
            report += "No articles were processed.\n"

        report += "===== END OF REPORT =====\n"
        return report

    def cleanup(self):
        log_service.external("Cleaning up threads and closing browsers")
        self.close_thread_browser()

    async def get_top_news(self, query, is_topic=False, language='en', country='US', period='7d', top_n=10):
        try:
            log_service.external(f"News: Getting top {top_n} news articles for query: {query}")
            if is_topic and query.upper() in [cat.upper() for cat in self.CATEGORIES]:
                sorted_articles = await self.fetch_and_analyze_news(
                    query.upper(), is_topic=True, language=language, country=country,
                    period=period, max_results=top_n
                )
            else:
                sorted_articles = await self.fetch_and_analyze_news(
                    query, is_topic=False, language=language, country=country,
                    period=period, max_results=top_n
                )
            top_articles = sorted_articles[:top_n]
            final_report = self.generate_final_report(top_articles)
            log_service.external(f"News: {final_report}")
            return top_articles, final_report
        finally:
            self.cleanup()