Referral Monitor v9.9 - cleaner discovery and friendlier Telegram alerts

What changed:
- Includes the v9.8 immediate Telegram delivery and user-feedback controls.
- Podcast Index results are checked against the episode's real publication time.
- Old podcast episodes and episodes without a publication time no longer reach Telegram.
- YouTube searches the full last 6 hours on every poll, so late-indexed videos are found.
- A YouTube video is checked again if its description did not contain the referral yet.
- Telegram alerts now use plain language, a clear source, and simple action buttons.
- X replies containing a Claude referral link are detected and labelled as an X reply.
- An X reply without a referral link does not create a referral alert.

Required Render setting:
YOUTUBE_LOOKBACK_MINUTES=360

Recommended production settings:
VALIDATION_MAX_ATTEMPTS=1
VALIDATOR_BROWSER_FALLBACK_ENABLED=false
TELEGRAM_FEEDBACK_POLL_SECONDS=5

After applying the update:
1. Run pytest.
2. Commit and push to main.
3. Set YOUTUBE_LOOKBACK_MINUTES to 360 in Render Environment.
4. Wait for Render to deploy.
5. Open /version and confirm Referral Monitor v9.9.

Quick checks:
- Post a new referral in an X post and in an X reply. Telegram should label them differently.
- Upload a YouTube video with a unique referral in its title or description. It will remain
  eligible for discovery for 6 hours, even if YouTube indexes it late.
- Old podcast episodes may appear as ignored events in Render logs, but must not be sent.
