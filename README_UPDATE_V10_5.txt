Referral Monitor v10.5.0 - Apify Facebook/Instagram + Telegram key rotation

What changed
- Threads: still Scrape Creators, every 5 minutes.
- Reddit: still Scrape Creators, every 5 minutes.
- Facebook: broad public-post keyword search through Apify actor memo23/facebook-search-scraper, every 10 minutes.
- Instagram: broad public Posts + Reels keyword search through Apify actor scraping_solutions/instagram-boolean-search-scraper-posts-reels, every 15 minutes.
- Freshness is enforced locally after each Actor returns: 12 minutes for Facebook, 17 minutes for Instagram.
- Each Apify run has a default $0.10 hard cost cap.
- Apify token can be rotated from an authorized Telegram chat.
- A valid Telegram key update switches the running process immediately, persists APIFY_API_TOKEN to Render, and queues a deploy-only Render release.
- Telegram tries to delete the message containing the token after reading it. Use a private bot chat for key rotation.

Required Render variables
Existing variables:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_IDS
- SCRAPE_CREATORS_API_KEY

New variables:
- APIFY_API_TOKEN=<current Apify token>
- TELEGRAM_ADMIN_CHAT_IDS=<client private Telegram chat ID allowed to rotate the token>
- RENDER_API_KEY=<Render account API key that can update this service>
- RENDER_SERVICE_ID=<this referral-monitor service ID, starts with srv->

Recommended non-secret values
- APIFY_TIMEOUT_SECONDS=150
- APIFY_MAX_RUN_COST_USD=0.10
- APIFY_FACEBOOK_ACTOR_ID=memo23~facebook-search-scraper
- APIFY_INSTAGRAM_ACTOR_ID=scraping_solutions~instagram-boolean-search-scraper-posts-reels
- APIFY_RENDER_AUTO_DEPLOY=true
- FACEBOOK_WATCH_INTERVAL_SECONDS=600
- FACEBOOK_LOOKBACK_MINUTES=12
- FACEBOOK_QUERIES=claude referral
- FACEBOOK_SEARCH_LIMIT=10
- FACEBOOK_PAGE_DELAY_MS=800
- INSTAGRAM_WATCH_INTERVAL_SECONDS=900
- INSTAGRAM_LOOKBACK_MINUTES=17
- INSTAGRAM_QUERIES=claude referral
- INSTAGRAM_SEARCH_LIMIT=10
- INSTAGRAM_CONTENT_TYPE=posts_and_reels
- INSTAGRAM_SEARCH_COVERAGE=efficient
- INSTAGRAM_HASHTAG_FEED_TYPE=recent

Telegram admin commands
- /whoami
  Returns the current Telegram chat ID. Use this to fill TELEGRAM_ADMIN_CHAT_IDS.

- /apify_status
  Shows whether an Apify key is active and whether Render rotation is configured.

- /set_apify_token apify_api_xxx
  Validates the token against Apify, switches the current process immediately, updates the APIFY_API_TOKEN environment variable on Render, and queues a deploy-only release when APIFY_RENDER_AUTO_DEPLOY=true.

Important
- Only chats listed in TELEGRAM_ADMIN_CHAT_IDS can run /set_apify_token.
- Never commit real Apify, Telegram, Scrape Creators, or Render keys to GitHub.
- Use the client's private Telegram chat with the bot for key rotation rather than a public/group chat.
- Telegram key rotation changes credentials only. It does not create Apify accounts or generate keys.

Local test commands
python -m app.main status
python -m app.main facebook-test
python -m app.main instagram-test
python -m app.main telegram-test

The social watcher unit tests added in this update can be run with:
pytest -q tests/test_apify_social.py tests/test_telegram_admin.py
