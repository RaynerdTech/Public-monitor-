Referral Monitor v8.1 hotfix

- Drops Exa candidate URLs that are definitively dead (HTTP 404 or 410).
- Prevents stale Exa-cached referral text from being processed when the live page no longer exists.
- Keeps non-definitive fetch failures (403/429/network) as discovery candidates because the page may still be live but bot-protected.
- Adds a regression test for stale/deleted Exa results.
