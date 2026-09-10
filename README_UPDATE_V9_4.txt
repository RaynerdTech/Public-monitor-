Referral Monitor v9.4 — unified Render runner

What changed
- Adds `python -m app.main watch-all`.
- On Render, the same Web Service now binds the HTTP port for /health and Threads OAuth while also running every configured watcher.
- X, YouTube, Exa/Web, Podcast/RSS, Reddit (when credentials are approved/configured), and Threads (after OAuth) run as independent tasks.
- A failure in one watcher no longer takes down the other source watchers.
- Threads waits for the OAuth token and starts automatically as soon as authorization completes.
- Render start command changed to: python -m app.main watch-all

Render Free is suitable for callback/integration testing only because Render may spin down inactive free web services. Use an always-on paid service for the final 24/7 monitor.
