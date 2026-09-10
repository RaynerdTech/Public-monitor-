Referral Monitor v7.3 - browser-backed Claude referral validation

What changed
- Direct Claude referral validation still runs first.
- Cloudflare 403 challenge responses are now classified as blocked instead of generic error.
- When Cloudflare challenges the direct request, validation automatically falls back to a real Chrome/Edge browser session using Playwright.
- Browser state is persisted in .referral-browser-profile so normal Cloudflare clearance can be reused.
- Added validator-test command to validate a URL/code without touching dedupe/database state.
- Added revalidate command to re-check an already stored referral and optionally send it to Telegram when it becomes valid.
- is_valid=false is now classified as inactive.
- Added browser fallback status information to the status command.
- Added Playwright and tzdata to requirements.txt.

Default browser fallback settings
VALIDATOR_BROWSER_FALLBACK_ENABLED=true
VALIDATOR_BROWSER_HEADLESS=false
VALIDATOR_BROWSER_CHANNEL=chrome
VALIDATOR_BROWSER_PROFILE_DIR=.referral-browser-profile
VALIDATOR_BROWSER_CHALLENGE_WAIT_SECONDS=120
VALIDATOR_BROWSER_NAVIGATION_TIMEOUT_SECONDS=30

These have defaults in code, so the existing .env does not have to be edited for the first test.

After applying this update
1. Install/update dependencies:
   python -m pip install -r requirements.txt

2. Run tests and status:
   pytest
   python -m app.main status

3. Test the referral already found on YouTube:
   python -m app.main validator-test Vck1Go3v3w

If Claude presents a normal Cloudflare check, Chrome will open and the validator waits for it to clear. Complete the normal browser check if prompted. The program then fetches Claude's referral API from inside that browser session.

4. Revalidate the stored YouTube referral and send it only if valid:
   python -m app.main revalidate Vck1Go3v3w --telegram

The same validate_referral function is used by X, YouTube, Reddit, web watchers and future sources, so the fallback is global rather than YouTube-specific.
