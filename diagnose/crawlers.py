"""AI 爬虫名单（UA + 类别 + 运营方）— robots 声明与 bots 实测共用。

五类（2026-11 增 extension）：
  live-retrieval   用户提问时实时抓取（ChatGPT-User 等）
  search-index     为 AI 搜索建索引（OAI-SearchBot、PerplexityBot、Claude-SearchBot）
  traditional-search  传统搜索（Googlebot、Bingbot，驱动 AIO/Copilot）
  training         训练模型用（GPTBot、CCBot，拦截对 AI 可见性影响小）
  extension        扩展生态抓取（Amazonbot：Alexa/电商生态，非 AI 搜索核心通路，
                   被拦仅 P3，不得判为核心 GEO 阻塞）
"""
from __future__ import annotations

AI_CRAWLERS = {
    "GPTBot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.4; +https://openai.com/gptbot)", "class": "training", "operator": "OpenAI"},
    "OAI-SearchBot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; OAI-SearchBot/1.4; +https://openai.com/searchbot)", "class": "search-index", "operator": "OpenAI"},
    "ChatGPT-User": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ChatGPT-User/1.0; +https://openai.com/bot)", "class": "live-retrieval", "operator": "OpenAI"},
    "ClaudeBot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +https://www.anthropic.com/claude-bot)", "class": "training", "operator": "Anthropic"},
    "Claude-SearchBot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://www.anthropic.com/claudebot)", "class": "search-index", "operator": "Anthropic"},
    "Claude-User": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +https://www.anthropic.com/claudebot)", "class": "live-retrieval", "operator": "Anthropic"},
    "PerplexityBot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "class": "search-index", "operator": "Perplexity"},
    "Perplexity-User": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)", "class": "live-retrieval", "operator": "Perplexity"},
    "GoogleBot": {"ua": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "class": "traditional-search", "operator": "Google"},
    "BingBot": {"ua": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "class": "traditional-search", "operator": "Microsoft"},
    "CCBot": {"ua": "CCBot/2.0 (+https://commoncrawl.org/faq/)", "class": "training", "operator": "Common Crawl"},
    "Amazonbot": {"ua": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amazonbot/0.1) Chrome/119.0.0.0 Safari/537.36", "class": "extension", "operator": "Amazon"},
}

BOT_CLASSES = ("live-retrieval", "search-index", "traditional-search", "training", "extension")
