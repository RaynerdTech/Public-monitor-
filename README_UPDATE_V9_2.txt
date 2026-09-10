Referral Monitor v9.2 - hosted Threads OAuth service

Adds:
- Persistent `threads-web-server` command for cloud hosting.
- Binds to 0.0.0.0 and automatically uses Railway's PORT environment variable.
- `/health` endpoint for hosting health checks.
- `/threads/authorize` starts Threads OAuth and redirects to Meta.
- `/threads/callback` completes the authorization and stores the long-lived token.
- Hosted server stays alive after authorization instead of exiting.
- Optional THREADS_OAUTH_START_KEY protects the authorization-start endpoint.
- THREADS_REDIRECT_URI can be set explicitly; Railway public domain can also be resolved automatically once available.

Recommended Railway start command:
python -m app.main threads-web-server

Recommended persistent volume mount:
/data

Recommended Railway variables for persistence:
THREADS_TOKEN_FILE=/data/.threads-token.json
DATABASE_PATH=/data/referrals.db
PODCAST_SOURCE_REGISTRY=/data/podcast_sources.json
VALIDATOR_BROWSER_PROFILE_DIR=/data/browser-profile
