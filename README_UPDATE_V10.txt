REFERRAL MONITOR V10.0 - DURABLE THREADS OAUTH
================================================

WHAT THIS FIXES
---------------
The Threads watcher was polling correctly every 30 seconds, but its OAuth token
was saved only in .threads-token.json. Render's free service filesystem is
ephemeral, so a deployment or restart removed that file and paused Threads.

V10.0 keeps the local token for immediate use and also saves the complete token
record in this service's Render environment as THREADS_TOKEN_RECORD. It updates
only that one variable; it does not replace or delete your other variables.
After OAuth or an automatic token refresh, it queues a deploy-only release so
the durable token is active on every future process start.

ONE-TIME RENDER SETUP (DO THIS BEFORE CLIENT AUTHORIZATION)
-----------------------------------------------------------
1. Deploy/upload the V10.0 update files.
2. In this Render service's Environment page, add:

   THREADS_RENDER_API_KEY=<a Render API key with access to this service>
   THREADS_RENDER_SERVICE_ID=<the srv-... ID from this service's dashboard URL>
   THREADS_RENDER_AUTO_DEPLOY=true

3. Keep the existing THREADS_APP_ID, THREADS_APP_SECRET,
   THREADS_OAUTH_START_KEY, and redirect URI settings unchanged.
4. Do not manually set THREADS_TOKEN_RECORD. The app creates and maintains it.
5. Let this deployment finish completely.
6. Only then send the client the protected /threads/authorize?key=... URL.

WHAT HAPPENS AFTER AUTHORIZATION
--------------------------------
- The callback exchanges Meta's one-use authorization code for a long-lived
  Threads token.
- The token is never printed to logs or placed in the client-facing URL.
- The app saves THREADS_TOKEN_RECORD in Render and queues a deploy-only release.
- On the new process, Threads loads the durable record and resumes polling.
- The app refreshes the long-lived token when it is within seven days of expiry
  and persists the refreshed record the same way.
- Telegram receives one warning if the token is ever missing or expired and
  administrator reauthorization is required.

EXPECTED LOGS
-------------
Successful authorization:
  threads_token_persisted (backend=render_environment)
  threads_oauth_completed (durable=true)

After the deploy-only release:
  Threads watcher active
  source_poll_started (source=Threads)
  source_poll_completed (source=Threads)

If you see threads_token_persistence_failed, do not perform another normal
deployment yet. Correct the Render API key/service ID first; the local token is
working only for the current process in that state.

IMPORTANT
---------
The old authorization code cannot be reused or embedded in a new authorization
URL. Meta creates that short-lived, one-time code only after the account owner
approves the current OAuth request, and the callback exchanges it server-side.

No credentials or access tokens are included in this update archive.
