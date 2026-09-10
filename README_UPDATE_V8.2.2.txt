Referral Monitor v8.2.2 hotfix

- Keeps v8.2 strict recent-only Exa filtering.
- Raises Exa request timeout from 20s to 45s.
- Retries transient Exa timeout/network failures up to 3 times automatically.
- web-test now prints the actual exception type (for example ReadTimeout) instead of a blank "Exa request failed" message.
- No .env changes required.
