Referral Monitor v10.4.1 - Fresh social results only

This is an incremental update for v10.4.

Changes:
- Threads: 5-minute polling, 7-minute publication lookback (5 min + 2 min overlap).
- Reddit: 5-minute polling, 7-minute publication lookback.
- Instagram: 15-minute polling, 17-minute publication lookback.
- Facebook page/group monitoring: 10-minute polling, 12-minute publication lookback.
- Threads requests also send start_date/end_date to reduce old search results before local filtering.
- Posts with missing timestamps are ignored by these social watchers so an undated historical result cannot be treated as new.
- Status output now shows each social lookback window.

The small 2-minute overlap is intentional. It reduces gaps from clock skew and API indexing delay while still keeping alerts very recent. Existing deduplication prevents the overlap from sending the same post twice.

No changes were made to the older YouTube/Web/Podcast lookback strategy because those sources are known to index late and tightening them to their polling interval would cause missed referrals.
