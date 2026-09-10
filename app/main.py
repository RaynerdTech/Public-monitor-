import asyncio
import html
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import typer
from rich.console import Console
from rich.table import Table

from app.config import (
    EXA_WATCH_INTERVAL_SECONDS,
    EXA_SEARCH_TYPE,
    EXA_SEARCH_LIMIT,
    EXA_QUERIES,
    EXA_PAGE_FETCH_TIMEOUT_SECONDS,
    EXA_LOOKBACK_MINUTES,
    EXA_API_KEY,
    PODCAST_DISCOVERY_INTERVAL_SECONDS,
    PODCAST_DISCOVERY_QUERIES,
    PODCAST_INDEX_API_KEY,
    PODCAST_INDEX_API_SECRET,
    PODCAST_INDEX_RECENT_MAX,
    PODCAST_INDEX_USER_AGENT,
    PODCAST_INDEX_WATCH_INTERVAL_SECONDS,
    PODCAST_LOOKBACK_MINUTES,
    PODCAST_RSS_INTERVAL_SECONDS,
    PODCAST_RSS_MAX_FEEDS,
    PODCAST_SOURCE_REGISTRY,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_IDS,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_QUERIES,
    REDDIT_SEARCH_LIMIT,
    REDDIT_USER_AGENT,
    REDDIT_WATCH_INTERVAL_SECONDS,
    THREADS_ACCESS_TOKEN,
    THREADS_APP_ID,
    THREADS_APP_SECRET,
    THREADS_OAUTH_HOST,
    THREADS_OAUTH_PORT,
    THREADS_OAUTH_SCOPES,
    THREADS_REDIRECT_URI,
    THREADS_OAUTH_START_KEY,
    THREADS_TOKEN_FILE,
    THREADS_QUERIES,
    THREADS_SEARCH_LIMIT,
    THREADS_WATCH_INTERVAL_SECONDS,
    URL_WATCH_INTERVAL_SECONDS,
    VALIDATOR_BROWSER_FALLBACK_ENABLED,
    VALIDATOR_BROWSER_HEADLESS,
    VALIDATOR_BROWSER_CHANNEL,
    YOUTUBE_API_KEY,
    YOUTUBE_LOOKBACK_MINUTES,
    YOUTUBE_QUERIES,
    YOUTUBE_SEARCH_LIMIT,
    YOUTUBE_WATCH_INTERVAL_SECONDS,
    X_BEARER_TOKEN,
    X_QUERIES,
    X_SEARCH_LIMIT,
    X_STREAM_RECONNECT_SECONDS,
    X_STREAM_RULES,
    X_WATCH_INTERVAL_SECONDS,
)
from app.core.database import get_recent_referrals, get_referral_by_code, init_db, update_validation
from app.core.extractor import extract_referral_code, extract_referral_links
from app.services.pipeline import process_post
from app.services.threads_oauth import (
    build_threads_authorization_url,
    create_token_record,
    exchange_code_for_short_lived_token,
    exchange_for_long_lived_token,
    fetch_threads_identity,
    load_threads_token,
    new_oauth_state,
    refresh_token_record,
    save_threads_token,
    resolve_threads_redirect_uri,
    resolve_threads_server_port,
)
from app.services.telegram import (
    format_referral_alert,
    get_telegram_updates,
    send_telegram_message,
    send_telegram_message_all,
)
from app.services.validator import ValidationResult, validate_referral
from app.watchers.base import SourcePost
from app.watchers.file import FileWatcher
from app.watchers.podcast import PodcastWatcher
from app.watchers.reddit import RedditWatcher
from app.watchers.threads import ThreadsWatcher
from app.watchers.url import UrlWatcher
from app.watchers.youtube import YouTubeWatcher
from app.watchers.web import ExaWebWatcher
from app.watchers.x import XFilteredStreamWatcher, XWatcher


cli = typer.Typer(no_args_is_help=True)
console = Console()


def _print_results(results) -> None:
    if not results:
        return

    table = Table("Referral", "Status", "Campaign", "New?")
    for candidate, validation, is_new in results:
        table.add_row(
            candidate.referral_url,
            validation.status,
            validation.campaign or "-",
            "yes" if is_new else "no",
        )
    console.print(table)


def _threads_token_record():
    return load_threads_token(THREADS_TOKEN_FILE)


def _threads_current_token(*, refresh_if_needed: bool = False) -> str:
    if THREADS_ACCESS_TOKEN:
        return THREADS_ACCESS_TOKEN

    record = _threads_token_record()
    if not record or record.is_expired():
        return ""

    if refresh_if_needed and record.needs_refresh() and THREADS_APP_SECRET:
        try:
            record = refresh_token_record(record=record)
            save_threads_token(THREADS_TOKEN_FILE, record)
        except Exception:
            # Keep using the still-valid token if a refresh attempt fails.
            pass

    return record.access_token


def _threads_configured() -> bool:
    return bool(_threads_current_token() and THREADS_QUERIES)


def _threads_oauth_configured() -> bool:
    return bool(THREADS_APP_ID and THREADS_APP_SECRET and THREADS_OAUTH_SCOPES)


def _reddit_configured() -> bool:
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT and REDDIT_QUERIES)


def _youtube_configured() -> bool:
    return bool(YOUTUBE_API_KEY and YOUTUBE_QUERIES)


def _x_configured() -> bool:
    return bool(X_BEARER_TOKEN and X_QUERIES)


def _web_configured() -> bool:
    return bool(EXA_API_KEY and EXA_QUERIES)


def _podcast_configured() -> bool:
    return bool(PODCAST_INDEX_API_KEY and PODCAST_INDEX_API_SECRET)


@cli.command("scan")
def scan(
    text: str = typer.Argument(..., help="Text containing Claude referral links"),
    source: str = typer.Option("manual", help="Source name"),
    source_url: str | None = typer.Option(None, help="Original post/page URL"),
) -> None:
    async def run() -> None:
        post = SourcePost(source=source, text=text, url=source_url)
        results = await process_post(post)
        _print_results(results)
        if not results:
            console.print("[yellow]No Claude referral links found.[/yellow]")

    asyncio.run(run())


@cli.command("simulate")
def simulate(
    referral_url: str = typer.Argument(..., help="Referral URL to simulate"),
    source: str = typer.Option("test-source", help="Fake source name"),
    source_url: str = typer.Option(
        "https://example.com/test-post",
        help="Fake source URL",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Also send a clearly labelled Telegram test alert",
    ),
) -> None:
    async def fake_validator(_: str) -> ValidationResult:
        return ValidationResult(
            status="valid",
            campaign="SIMULATED_GUEST_PASS",
            is_valid=True,
            message="Simulation only",
        )

    async def run() -> None:
        post = SourcePost(
            source=source,
            text=referral_url,
            url=source_url,
            created_at=datetime.now(timezone.utc),
        )
        results = await process_post(post, validator=fake_validator, alert_valid=False)
        _print_results(results)

        if telegram and results:
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
                console.print("[red]Telegram is not configured in .env.[/red]")
                raise typer.Exit(1)
            candidate, validation, _ = results[0]
            alert = format_referral_alert(candidate, validation, simulated=True)
            await send_telegram_message_all(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, alert)
            console.print("[green]Telegram test alert sent.[/green]")

    asyncio.run(run())


@cli.command("validator-test")
def validator_test(
    referral: str = typer.Argument(..., help="Claude referral URL or referral code"),
) -> None:
    """Validate one referral directly, using browser fallback if Cloudflare challenges it."""

    async def run() -> None:
        code = extract_referral_code(referral) or referral.strip().strip("/")
        if not code:
            console.print("[red]Could not extract a referral code.[/red]")
            raise typer.Exit(1)

        console.print(f"Validating referral code: [cyan]{code}[/cyan]")
        result = await validate_referral(code)
        table = Table("Status", "Campaign", "Method", "Message")
        table.add_row(
            result.status,
            result.campaign or "-",
            result.method,
            result.message or "-",
        )
        console.print(table)

    asyncio.run(run())


@cli.command("revalidate")
def revalidate(
    referral: str = typer.Argument(..., help="Stored Claude referral URL or referral code"),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Send Telegram if the stored referral now validates as usable",
    ),
) -> None:
    """Revalidate an existing database referral without being blocked by dedupe."""

    async def run() -> None:
        code = extract_referral_code(referral) or referral.strip().strip("/")
        row = await get_referral_by_code(code)
        if not row:
            console.print(f"[red]Referral code {code} is not stored in the local database.[/red]")
            raise typer.Exit(1)

        result = await validate_referral(code)
        await update_validation(
            referral_code=code,
            status=result.status,
            campaign=result.campaign,
            validation_message=result.message,
        )

        table = Table("Referral", "Status", "Campaign", "Method", "Message")
        table.add_row(
            row["referral_url"],
            result.status,
            result.campaign or "-",
            result.method,
            result.message or "-",
        )
        console.print(table)

        if telegram and result.status == "valid":
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
                console.print("[red]Telegram is not configured in .env.[/red]")
                raise typer.Exit(1)

            def parse_dt(value):
                return datetime.fromisoformat(value) if value else None

            from app.core.models import ReferralCandidate

            candidate = ReferralCandidate(
                referral_url=row["referral_url"],
                referral_code=row["referral_code"],
                source=row["source"],
                source_url=row["source_url"],
                post_created_at=parse_dt(row["post_created_at"]),
                detected_at=parse_dt(row["detected_at"]) or datetime.now(timezone.utc),
            )
            alert = format_referral_alert(candidate, result)
            await send_telegram_message_all(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, alert)
            console.print("[green]Valid referral sent to Telegram.[/green]")
        elif telegram:
            console.print("[yellow]Telegram was not sent because the referral is not valid.[/yellow]")

    asyncio.run(run())


@cli.command("watch-file")
def watch_file(
    path: str = typer.Argument("watch-input.txt", help="Local text file to monitor"),
    interval: int = typer.Option(2, help="Polling interval in seconds"),
) -> None:
    async def run() -> None:
        watcher = FileWatcher(path, interval)
        console.print(f"[green]Watching {path} every {interval}s. Ctrl+C to stop.[/green]")
        async for post in watcher.stream():
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher stopped.[/yellow]")


@cli.command("watch-url")
def watch_url(
    url: str = typer.Argument(..., help="Public page to monitor"),
    interval: int = typer.Option(
        URL_WATCH_INTERVAL_SECONDS,
        help="Polling interval in seconds",
    ),
) -> None:
    async def run() -> None:
        watcher = UrlWatcher(url, interval)
        console.print(f"[green]Watching {url} every {interval}s. Ctrl+C to stop.[/green]")
        async for post in watcher.stream():
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Watcher stopped.[/yellow]")


@cli.command("threads-oauth-server")
def threads_oauth_server(
    redirect_uri: str = typer.Option(
        ...,
        "--redirect-uri",
        help="Exact public HTTPS callback URL registered in the Threads app",
    ),
    port: int = typer.Option(
        THREADS_OAUTH_PORT,
        min=1,
        max=65535,
        help="Local callback server port exposed through the HTTPS tunnel",
    ),
) -> None:
    """Run the local Threads OAuth callback and save a long-lived token."""
    if not _threads_oauth_configured():
        console.print(
            "[red]Set THREADS_APP_ID and THREADS_APP_SECRET in .env first.[/red]"
        )
        raise typer.Exit(1)

    parsed = urlparse(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc:
        console.print("[red]--redirect-uri must be a complete HTTPS URL.[/red]")
        raise typer.Exit(1)

    callback_path = parsed.path or "/threads/callback"
    state = new_oauth_state()
    auth_url = build_threads_authorization_url(
        THREADS_APP_ID,
        redirect_uri,
        THREADS_OAUTH_SCOPES,
    THREADS_REDIRECT_URI,
    THREADS_OAUTH_START_KEY,
        state,
    )
    result: dict[str, str | bool] = {"done": False, "message": ""}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _send_html(self, status: int, title: str, body: str) -> None:
            content = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{html.escape(title)}</title></head>"
                "<body style='font-family:system-ui;max-width:720px;margin:60px auto;padding:0 20px'>"
                f"<h2>{html.escape(title)}</h2><p>{html.escape(body)}</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802
            request_url = urlparse(self.path)
            if request_url.path == "/":
                self._send_html(200, "Referral Monitor", "Threads OAuth callback is running.")
                return
            if request_url.path != callback_path:
                self._send_html(404, "Not found", "This is not the configured Threads callback path.")
                return

            params = parse_qs(request_url.query)
            returned_state = (params.get("state") or [""])[0]
            error = (params.get("error") or params.get("error_message") or [""])[0]
            code = (params.get("code") or [""])[0]

            if error:
                result["done"] = True
                result["message"] = f"Threads authorization failed: {error}"
                self._send_html(400, "Threads authorization failed", str(error))
                return
            if not returned_state or returned_state != state:
                result["done"] = True
                result["message"] = "Threads OAuth state check failed"
                self._send_html(400, "Invalid OAuth state", "The authorization response could not be verified.")
                return
            if not code:
                result["done"] = True
                result["message"] = "Threads did not return an authorization code"
                self._send_html(400, "Missing authorization code", "Threads did not return an authorization code.")
                return

            try:
                short_token, short_user_id = exchange_code_for_short_lived_token(
                    app_id=THREADS_APP_ID,
                    app_secret=THREADS_APP_SECRET,
                    code=code,
                    redirect_uri=redirect_uri,
                )
                long_token, expires_in, token_type = exchange_for_long_lived_token(
                    short_lived_token=short_token,
                    app_secret=THREADS_APP_SECRET,
                )
                user_id, username = fetch_threads_identity(access_token=long_token)
                record = create_token_record(
                    access_token=long_token,
                    user_id=user_id or short_user_id,
                    username=username,
                    token_type=token_type,
                    expires_in=expires_in,
                )
                save_threads_token(THREADS_TOKEN_FILE, record)
                result["done"] = True
                result["message"] = (
                    f"Authorized @{username}" if username else "Threads authorization completed"
                )
                self._send_html(
                    200,
                    "Threads connected",
                    "Authorization completed successfully. You can close this page now.",
                )
            except Exception as exc:  # callback must return a useful browser response
                result["done"] = True
                result["message"] = f"Token exchange failed: {type(exc).__name__}: {exc}"
                self._send_html(500, "Token exchange failed", str(exc))

    server = ThreadingHTTPServer((THREADS_OAUTH_HOST, port), Handler)
    server.timeout = 1

    console.print(f"[green]Threads OAuth callback listening locally on http://{THREADS_OAUTH_HOST}:{port}[/green]")
    console.print("Add this exact URL to the client's Threads app Redirect Callback URLs:")
    console.print(f"[bold cyan]{redirect_uri}[/bold cyan]")
    console.print("\nAfter Meta saves that callback, send/open this authorization URL:")
    console.print(f"[bold cyan]{auth_url}[/bold cyan]")
    console.print("\nWaiting for the Threads authorization callback. Ctrl+C to stop.")

    try:
        while not result["done"]:
            server.handle_request()
    except KeyboardInterrupt:
        console.print("\n[yellow]Threads OAuth server stopped.[/yellow]")
        return
    finally:
        server.server_close()

    message = str(result["message"])
    if message.startswith("Authorized") or message == "Threads authorization completed":
        console.print(f"[green]{message}.[/green]")
        console.print(
            f"Long-lived token saved locally to {THREADS_TOKEN_FILE}. The token was not printed."
        )
        console.print("Run: python -m app.main threads-test")
    else:
        console.print(f"[red]{message}[/red]")
        raise typer.Exit(1)


@cli.command("threads-web-server")
def threads_web_server() -> None:
    """Run a persistent public Threads OAuth web service for hosted deployments."""
    oauth_configured = _threads_oauth_configured()

    try:
        redirect_uri = resolve_threads_redirect_uri(
            configured_uri=THREADS_REDIRECT_URI,
            railway_public_domain=os.getenv("RAILWAY_PUBLIC_DOMAIN", ""),
        )
        port = resolve_threads_server_port(
            port_env=os.getenv("PORT", ""),
            fallback=THREADS_OAUTH_PORT,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    pending_states: dict[str, datetime] = {}

    def _prune_states() -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - 15 * 60
        stale = [
            state
            for state, created in pending_states.items()
            if created.timestamp() < cutoff
        ]
        for state in stale:
            pending_states.pop(state, None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _send_html(self, status: int, title: str, body: str) -> None:
            content = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{html.escape(title)}</title></head>"
                "<body style='font-family:system-ui;max-width:720px;margin:60px auto;padding:0 20px'>"
                f"<h2>{html.escape(title)}</h2><p>{html.escape(body)}</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            request_url = urlparse(self.path)

            if request_url.path in {"/", "/health"}:
                self._send_html(200, "Referral Monitor", "Threads OAuth service is running.")
                return

            if request_url.path == "/threads/authorize":
                if not oauth_configured:
                    self._send_html(
                        503,
                        "Threads OAuth not ready",
                        "The Threads App ID/secret and OAuth scopes have not been configured yet.",
                    )
                    return
                if not redirect_uri:
                    self._send_html(
                        503,
                        "Threads OAuth not ready",
                        "The public redirect URI has not been configured yet.",
                    )
                    return

                params = parse_qs(request_url.query)
                supplied_key = (params.get("key") or [""])[0]
                if THREADS_OAUTH_START_KEY and supplied_key != THREADS_OAUTH_START_KEY:
                    self._send_html(403, "Forbidden", "Invalid authorization start key.")
                    return

                _prune_states()
                state = new_oauth_state()
                pending_states[state] = datetime.now(timezone.utc)
                auth_url = build_threads_authorization_url(
                    THREADS_APP_ID,
                    redirect_uri,
                    THREADS_OAUTH_SCOPES,
                    state,
                )
                self._redirect(auth_url)
                return

            if request_url.path != "/threads/callback":
                self._send_html(404, "Not found", "Unknown endpoint.")
                return

            if not oauth_configured:
                self._send_html(
                    503,
                    "Threads OAuth not ready",
                    "The Threads App ID/secret and OAuth scopes have not been configured yet.",
                )
                return
            if not redirect_uri:
                self._send_html(
                    503,
                    "Threads OAuth not ready",
                    "The public redirect URI has not been configured yet.",
                )
                return

            params = parse_qs(request_url.query)
            returned_state = (params.get("state") or [""])[0]
            error = (params.get("error") or params.get("error_message") or [""])[0]
            code = (params.get("code") or [""])[0]

            if error:
                self._send_html(400, "Threads authorization failed", str(error))
                return

            _prune_states()
            if not returned_state or returned_state not in pending_states:
                self._send_html(
                    400,
                    "Invalid OAuth state",
                    "The authorization response could not be verified. Start authorization again.",
                )
                return
            pending_states.pop(returned_state, None)

            if not code:
                self._send_html(
                    400,
                    "Missing authorization code",
                    "Threads did not return an authorization code.",
                )
                return

            try:
                short_token, short_user_id = exchange_code_for_short_lived_token(
                    app_id=THREADS_APP_ID,
                    app_secret=THREADS_APP_SECRET,
                    code=code,
                    redirect_uri=redirect_uri,
                )
                long_token, expires_in, token_type = exchange_for_long_lived_token(
                    short_lived_token=short_token,
                    app_secret=THREADS_APP_SECRET,
                )
                user_id, username = fetch_threads_identity(access_token=long_token)
                record = create_token_record(
                    access_token=long_token,
                    user_id=user_id or short_user_id,
                    username=username,
                    token_type=token_type,
                    expires_in=expires_in,
                )
                save_threads_token(THREADS_TOKEN_FILE, record)
                display_user = f"@{username}" if username else "Threads account"
                self._send_html(
                    200,
                    "Threads connected",
                    f"{display_user} was authorized successfully. You can close this page.",
                )
                console.print(f"[green]Threads OAuth completed for {display_user}.[/green]")
            except Exception as exc:
                console.print(f"[red]Threads token exchange failed: {type(exc).__name__}: {exc}[/red]")
                self._send_html(
                    500,
                    "Token exchange failed",
                    "Threads authorization reached the server, but token exchange failed.",
                )

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    console.print(f"[green]Threads web service listening on 0.0.0.0:{port}[/green]")
    if not oauth_configured:
        console.print(
            "[yellow]Threads App ID/secret not configured yet. The health endpoint is live, but authorization stays disabled until those variables are added.[/yellow]"
        )
    if redirect_uri:
        console.print(f"Callback URL: [cyan]{redirect_uri}[/cyan]")
        auth_path = "/threads/authorize"
        if THREADS_OAUTH_START_KEY:
            auth_path += f"?key={THREADS_OAUTH_START_KEY}"
        console.print(f"Authorization path: [cyan]{auth_path}[/cyan]")
    else:
        console.print(
            "[yellow]No public callback URL yet. Generate the hosting domain, then set THREADS_REDIRECT_URI and redeploy.[/yellow]"
        )

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        console.print("\n[yellow]Threads web service stopped.[/yellow]")
    finally:
        server.server_close()


@cli.command("threads-token-status")
def threads_token_status() -> None:
    """Show stored Threads token metadata without printing the token."""
    if THREADS_ACCESS_TOKEN:
        console.print("[green]THREADS_ACCESS_TOKEN is configured directly in .env.[/green]")
        return

    record = _threads_token_record()
    if not record:
        console.print(f"[yellow]No stored Threads token found at {THREADS_TOKEN_FILE}.[/yellow]")
        raise typer.Exit(1)

    table = Table("Field", "Value")
    table.add_row("User", f"@{record.username}" if record.username else "-")
    table.add_row("User ID", record.user_id or "-")
    table.add_row("Expires", record.expires_at or "unknown")
    table.add_row("Expired", "yes" if record.is_expired() else "no")
    table.add_row("Stored at", THREADS_TOKEN_FILE)
    console.print(table)


@cli.command("threads-test")
def threads_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured Threads search query",
    ),
) -> None:
    async def run() -> None:
        token = _threads_current_token(refresh_if_needed=True)
        if not token:
            console.print(
                "[red]Threads token is not configured. Run threads-oauth-server or set "
                "THREADS_ACCESS_TOKEN.[/red]"
            )
            raise typer.Exit(1)

        queries = [query] if query else THREADS_QUERIES
        watcher = ThreadsWatcher(
            token,
    THREADS_APP_ID,
    THREADS_APP_SECRET,
    THREADS_OAUTH_HOST,
    THREADS_OAUTH_PORT,
    THREADS_OAUTH_SCOPES,
    THREADS_REDIRECT_URI,
    THREADS_OAUTH_START_KEY,
    THREADS_TOKEN_FILE,
            queries,
            interval_seconds=THREADS_WATCH_INTERVAL_SECONDS,
            limit=THREADS_SEARCH_LIMIT,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400]
            console.print(
                f"[red]Threads API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]Threads request failed:[/red] {exc}")
            raise typer.Exit(1)

        table = Table("Source", "Posted", "URL", "Referral in text?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(f"[green]Threads API working. {len(posts)} recent posts returned.[/green]")

    asyncio.run(run())


@cli.command("watch-threads")
def watch_threads(
    interval: int = typer.Option(
        THREADS_WATCH_INTERVAL_SECONDS,
        min=10,
        help="Polling interval in seconds",
    ),
) -> None:
    if not _threads_configured():
        console.print(
            "[red]Threads is not configured. Set THREADS_ACCESS_TOKEN and "
            "THREADS_QUERIES in .env.[/red]"
        )
        raise typer.Exit(1)

    async def run() -> None:
        watcher = ThreadsWatcher(
            THREADS_ACCESS_TOKEN,
    THREADS_APP_ID,
    THREADS_APP_SECRET,
    THREADS_OAUTH_HOST,
    THREADS_OAUTH_PORT,
    THREADS_OAUTH_SCOPES,
    THREADS_REDIRECT_URI,
    THREADS_OAUTH_START_KEY,
    THREADS_TOKEN_FILE,
            THREADS_QUERIES,
            interval_seconds=interval,
            limit=THREADS_SEARCH_LIMIT,
        )
        console.print(
            "[green]Threads watcher started.[/green] "
            f"Queries: {', '.join(THREADS_QUERIES)} | interval: {interval}s"
        )
        console.print(
            "Valid new referrals will be stored and sent to Telegram when configured."
        )

        async for post in watcher.stream():
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Threads watcher stopped.[/yellow]")


@cli.command("reddit-test")
def reddit_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured Reddit search query",
    ),
) -> None:
    async def run() -> None:
        if not _reddit_configured():
            console.print(
                "[red]Reddit is not configured. Set REDDIT_CLIENT_ID, "
                "REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT in .env.[/red]"
            )
            raise typer.Exit(1)

        queries = [query] if query else REDDIT_QUERIES
        watcher = RedditWatcher(
            REDDIT_CLIENT_ID,
            REDDIT_CLIENT_SECRET,
            REDDIT_USER_AGENT,
            queries,
            interval_seconds=REDDIT_WATCH_INTERVAL_SECONDS,
            limit=REDDIT_SEARCH_LIMIT,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            console.print(
                f"[red]Reddit API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            if exc.response.status_code in {401, 403}:
                console.print(
                    "[yellow]Check the OAuth client values and confirm Reddit approved "
                    "this Data API use case.[/yellow]"
                )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]Reddit request failed:[/red] {exc}")
            raise typer.Exit(1)

        table = Table("Source", "Posted", "URL", "Referral in text?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(
            f"[green]Reddit API working. {len(posts)} recent matching posts returned.[/green]"
        )

    asyncio.run(run())


@cli.command("watch-reddit")
def watch_reddit(
    interval: int = typer.Option(
        REDDIT_WATCH_INTERVAL_SECONDS,
        min=10,
        help="Polling interval in seconds",
    ),
) -> None:
    if not _reddit_configured():
        console.print(
            "[red]Reddit is not configured. Set REDDIT_CLIENT_ID, "
            "REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT in .env.[/red]"
        )
        raise typer.Exit(1)

    async def run() -> None:
        watcher = RedditWatcher(
            REDDIT_CLIENT_ID,
            REDDIT_CLIENT_SECRET,
            REDDIT_USER_AGENT,
            REDDIT_QUERIES,
            interval_seconds=interval,
            limit=REDDIT_SEARCH_LIMIT,
        )
        console.print(
            "[green]Reddit watcher started.[/green] "
            f"Queries: {' | '.join(REDDIT_QUERIES)} | interval: {interval}s"
        )
        console.print(
            "New matching Reddit posts are passed through the same validator, "
            "dedupe and Telegram pipeline as X."
        )

        async for post in watcher.stream():
            console.print(
                f"[cyan]Reddit match:[/cyan] {post.source} "
                f"{post.created_at.isoformat() if post.created_at else ''}"
            )
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Reddit watcher stopped.[/yellow]")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        console.print(
            f"[red]Reddit watcher stopped with HTTP {exc.response.status_code}.[/red] {detail}"
        )
        raise typer.Exit(1)


@cli.command("youtube-test")
def youtube_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured YouTube search query",
    ),
) -> None:
    async def run() -> None:
        if not YOUTUBE_API_KEY:
            console.print("[red]Set YOUTUBE_API_KEY in .env first.[/red]")
            raise typer.Exit(1)

        queries = [query] if query else YOUTUBE_QUERIES
        watcher = YouTubeWatcher(
            YOUTUBE_API_KEY,
            queries,
            interval_seconds=YOUTUBE_WATCH_INTERVAL_SECONDS,
            max_results=YOUTUBE_SEARCH_LIMIT,
            lookback_minutes=YOUTUBE_LOOKBACK_MINUTES,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:700]
            console.print(
                f"[red]YouTube API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            if exc.response.status_code in {400, 403}:
                console.print(
                    "[yellow]Check that YouTube Data API v3 is enabled, the API key is "
                    "correct, and the key is restricted to YouTube Data API v3.[/yellow]"
                )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]YouTube request failed:[/red] {exc}")
            raise typer.Exit(1)

        table = Table("Source", "Posted", "URL", "Referral in text?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(
            f"[green]YouTube API working. {len(posts)} recent videos returned.[/green]"
        )
        console.print(
            "[yellow]Note: youtube-test uses one YouTube search request per configured query.[/yellow]"
        )

    asyncio.run(run())


@cli.command("youtube-validate-test")
def youtube_validate_test(
    query: str = typer.Option(
        "claude.ai/referral",
        help="YouTube query to fetch before validating discovered referral links",
    ),
    limit: int = typer.Option(
        5,
        min=1,
        max=50,
        help="Maximum number of discovered referral links to process",
    ),
    telegram: bool = typer.Option(
        False,
        "--telegram",
        help="Send Telegram alerts for referrals that validate as usable",
    ),
) -> None:
    """Run a small real YouTube -> validator -> dedupe pipeline test."""

    async def run() -> None:
        if not YOUTUBE_API_KEY:
            console.print("[red]Set YOUTUBE_API_KEY in .env first.[/red]")
            raise typer.Exit(1)

        watcher = YouTubeWatcher(
            YOUTUBE_API_KEY,
            [query],
            interval_seconds=YOUTUBE_WATCH_INTERVAL_SECONDS,
            max_results=YOUTUBE_SEARCH_LIMIT,
            lookback_minutes=YOUTUBE_LOOKBACK_MINUTES,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:700]
            console.print(
                f"[red]YouTube API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]YouTube request failed:[/red] {exc}")
            raise typer.Exit(1)

        referral_posts = [post for post in posts if extract_referral_links(post.text)]
        if not referral_posts:
            console.print(
                "[yellow]YouTube search worked, but no referral-containing videos were "
                "found in the configured lookback window.[/yellow]"
            )
            return

        processed = 0
        for post in referral_posts:
            if processed >= limit:
                break

            console.print(
                f"[cyan]YouTube validation test:[/cyan] {post.source} "
                f"{post.created_at.isoformat() if post.created_at else ''}"
            )
            console.print(f"Source: {post.url or '-'}")

            remaining = limit - processed
            links = extract_referral_links(post.text)[:remaining]
            if not links:
                continue

            # Process only the selected links so a description containing many URLs
            # cannot exceed the requested test limit.
            selected_post = SourcePost(
                source=post.source,
                text="\n".join(links),
                url=post.url,
                created_at=post.created_at,
            )
            results = await process_post(selected_post, alert_valid=telegram)
            _print_results(results)
            processed += len(results)

        console.print(
            f"[green]Processed {processed} YouTube referral candidate(s) through the "
            "real validator/dedupe pipeline.[/green]"
        )
        if telegram:
            console.print(
                "[green]Telegram was enabled for this test. Only status=valid referrals "
                "were eligible for an alert.[/green]"
            )
        else:
            console.print(
                "[yellow]Telegram was disabled for this historical test. Add --telegram "
                "if you want valid results sent to the configured chat.[/yellow]"
            )

    asyncio.run(run())


@cli.command("watch-youtube")
def watch_youtube(
    interval: int = typer.Option(
        YOUTUBE_WATCH_INTERVAL_SECONDS,
        min=60,
        help="Polling interval in seconds",
    ),
) -> None:
    if not _youtube_configured():
        console.print(
            "[red]YouTube is not configured. Set YOUTUBE_API_KEY and "
            "YOUTUBE_QUERIES in .env.[/red]"
        )
        raise typer.Exit(1)

    searches_per_day = (86400 / interval) * len(YOUTUBE_QUERIES)
    estimated_search_calls = int(searches_per_day + 0.999)

    async def run() -> None:
        watcher = YouTubeWatcher(
            YOUTUBE_API_KEY,
            YOUTUBE_QUERIES,
            interval_seconds=interval,
            max_results=YOUTUBE_SEARCH_LIMIT,
            lookback_minutes=YOUTUBE_LOOKBACK_MINUTES,
            status_callback=lambda message: console.print(f"[yellow]{message}[/yellow]"),
        )
        console.print(
            "[green]YouTube watcher started.[/green] "
            f"Queries: {' | '.join(YOUTUBE_QUERIES)} | interval: {interval}s"
        )
        console.print(
            f"Estimated YouTube search.list usage: ~{estimated_search_calls} calls/day. "
            "The current default search bucket is 100 calls/day."
        )
        if estimated_search_calls > 100:
            console.print(
                "[yellow]Warning: this interval/query count is above YouTube's current "
                "default 100 search.list calls/day. Increase the interval or request more search quota.[/yellow]"
            )
        console.print(
            "Recent matching videos are passed through the same referral extractor, "
            "validator, dedupe and Telegram pipeline as X."
        )

        async for post in watcher.stream():
            console.print(
                f"[cyan]YouTube match:[/cyan] {post.source} "
                f"{post.created_at.isoformat() if post.created_at else ''}"
            )
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]YouTube watcher stopped.[/yellow]")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:700]
        console.print(
            f"[red]YouTube watcher stopped with HTTP {exc.response.status_code}.[/red] {detail}"
        )
        raise typer.Exit(1)


@cli.command("web-test")
def web_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured Exa web search query",
    ),
    lookback_minutes: int = typer.Option(
        EXA_LOOKBACK_MINUTES,
        min=5,
        help="Ignore dated results older than this many minutes",
    ),
) -> None:
    async def run() -> None:
        if not EXA_API_KEY:
            console.print("[red]Set EXA_API_KEY in .env first.[/red]")
            raise typer.Exit(1)

        queries = [query] if query else EXA_QUERIES
        watcher = ExaWebWatcher(
            EXA_API_KEY,
    PODCAST_DISCOVERY_INTERVAL_SECONDS,
    PODCAST_DISCOVERY_QUERIES,
    PODCAST_INDEX_API_KEY,
    PODCAST_INDEX_API_SECRET,
    PODCAST_INDEX_RECENT_MAX,
    PODCAST_INDEX_USER_AGENT,
    PODCAST_INDEX_WATCH_INTERVAL_SECONDS,
    PODCAST_LOOKBACK_MINUTES,
    PODCAST_RSS_INTERVAL_SECONDS,
    PODCAST_RSS_MAX_FEEDS,
    PODCAST_SOURCE_REGISTRY,
            queries,
            interval_seconds=EXA_WATCH_INTERVAL_SECONDS,
            max_results=EXA_SEARCH_LIMIT,
            lookback_minutes=lookback_minutes,
            search_type=EXA_SEARCH_TYPE,
            page_fetch_timeout_seconds=EXA_PAGE_FETCH_TIMEOUT_SECONDS,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:700]
            console.print(
                f"[red]Exa API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            if exc.response.status_code in {401, 402, 403}:
                console.print(
                    "[yellow]Check the Exa API key and account billing/credits.[/yellow]"
                )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            detail = str(exc).strip() or "no additional message"
            console.print(
                f"[red]Exa request failed ({type(exc).__name__}):[/red] {detail}"
            )
            console.print(
                "[yellow]Transient Exa/network failures are retried automatically up to 3 times. "
                "If this repeats, check Exa dashboard status/credits and try again.[/yellow]"
            )
            raise typer.Exit(1)

        table = Table("Source", "Published", "URL", "Referral in page?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(
            f"[green]Exa web search working. {len(posts)} fresh candidate pages returned.[/green]"
        )

    asyncio.run(run())


@cli.command("watch-web")
def watch_web(
    interval: int = typer.Option(
        EXA_WATCH_INTERVAL_SECONDS,
        min=60,
        help="Broad web search interval in seconds",
    ),
) -> None:
    if not _web_configured():
        console.print(
            "[red]Web discovery is not configured. Set EXA_API_KEY and EXA_QUERIES in .env.[/red]"
        )
        raise typer.Exit(1)

    searches_per_day = (86400 / interval) * len(EXA_QUERIES)
    estimated_search_calls = int(searches_per_day + 0.999)

    async def run() -> None:
        watcher = ExaWebWatcher(
            EXA_API_KEY,
    PODCAST_DISCOVERY_INTERVAL_SECONDS,
    PODCAST_DISCOVERY_QUERIES,
    PODCAST_INDEX_API_KEY,
    PODCAST_INDEX_API_SECRET,
    PODCAST_INDEX_RECENT_MAX,
    PODCAST_INDEX_USER_AGENT,
    PODCAST_INDEX_WATCH_INTERVAL_SECONDS,
    PODCAST_LOOKBACK_MINUTES,
    PODCAST_RSS_INTERVAL_SECONDS,
    PODCAST_RSS_MAX_FEEDS,
    PODCAST_SOURCE_REGISTRY,
            EXA_QUERIES,
            interval_seconds=interval,
            max_results=EXA_SEARCH_LIMIT,
            lookback_minutes=EXA_LOOKBACK_MINUTES,
            search_type=EXA_SEARCH_TYPE,
            page_fetch_timeout_seconds=EXA_PAGE_FETCH_TIMEOUT_SECONDS,
            status_callback=lambda message: console.print(f"[yellow]{message}[/yellow]"),
        )
        console.print(
            "[green]Broad web watcher started.[/green] "
            f"Queries: {' | '.join(EXA_QUERIES)} | interval: {interval}s"
        )
        console.print(
            f"Estimated Exa Search usage: ~{estimated_search_calls} searches/day. "
            "Candidate pages are fetched directly and passed through the shared validator."
        )

        async for post in watcher.stream():
            referrals = extract_referral_links(post.text)
            if referrals:
                console.print(
                    f"[cyan]Web referral candidate:[/cyan] {post.url or '-'} "
                    f"{post.created_at.isoformat() if post.created_at else ''}"
                )
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Web watcher stopped.[/yellow]")


@cli.command("podcast-test")
def podcast_test() -> None:
    """Verify Podcast Index auth and run one discovery/recent/RSS cycle."""

    async def run() -> None:
        if not _podcast_configured():
            console.print(
                "[red]Set PODCAST_INDEX_API_KEY and PODCAST_INDEX_API_SECRET in .env first.[/red]"
            )
            raise typer.Exit(1)

        watcher = PodcastWatcher(
            PODCAST_INDEX_API_KEY,
            PODCAST_INDEX_API_SECRET,
            PODCAST_INDEX_USER_AGENT,
            interval_seconds=PODCAST_INDEX_WATCH_INTERVAL_SECONDS,
            recent_max=PODCAST_INDEX_RECENT_MAX,
            lookback_minutes=PODCAST_LOOKBACK_MINUTES,
            discovery_queries=PODCAST_DISCOVERY_QUERIES,
            discovery_interval_seconds=PODCAST_DISCOVERY_INTERVAL_SECONDS,
            rss_interval_seconds=PODCAST_RSS_INTERVAL_SECONDS,
            rss_max_feeds=PODCAST_RSS_MAX_FEEDS,
            source_registry_path=PODCAST_SOURCE_REGISTRY,
            status_callback=lambda message: console.print(f"[yellow]{message}[/yellow]"),
        )
        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:700]
            console.print(
                f"[red]Podcast Index returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            if exc.response.status_code in {401, 403}:
                console.print(
                    "[yellow]Check the Podcast Index API key/secret and system clock.[/yellow]"
                )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]Podcast request failed:[/red] {type(exc).__name__}: {exc}")
            raise typer.Exit(1)

        table = Table("Source", "Published", "URL", "Referral in notes?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(
            "[green]Podcast Index authentication/discovery worked.[/green] "
            f"{len(posts)} referral-containing podcast episode(s) found in this cycle."
        )
        console.print(
            f"RSS source registry: [cyan]{PODCAST_SOURCE_REGISTRY}[/cyan]. "
            "Matching/useful feeds are kept there automatically for direct polling."
        )

    asyncio.run(run())


@cli.command("watch-podcasts")
def watch_podcasts(
    interval: int = typer.Option(
        PODCAST_INDEX_WATCH_INTERVAL_SECONDS,
        min=30,
        help="Podcast Index recent-data polling interval in seconds",
    ),
) -> None:
    if not _podcast_configured():
        console.print(
            "[red]Podcasts are not configured. Set PODCAST_INDEX_API_KEY and "
            "PODCAST_INDEX_API_SECRET in .env.[/red]"
        )
        raise typer.Exit(1)

    async def run() -> None:
        watcher = PodcastWatcher(
            PODCAST_INDEX_API_KEY,
            PODCAST_INDEX_API_SECRET,
            PODCAST_INDEX_USER_AGENT,
            interval_seconds=interval,
            recent_max=PODCAST_INDEX_RECENT_MAX,
            lookback_minutes=PODCAST_LOOKBACK_MINUTES,
            discovery_queries=PODCAST_DISCOVERY_QUERIES,
            discovery_interval_seconds=PODCAST_DISCOVERY_INTERVAL_SECONDS,
            rss_interval_seconds=PODCAST_RSS_INTERVAL_SECONDS,
            rss_max_feeds=PODCAST_RSS_MAX_FEEDS,
            source_registry_path=PODCAST_SOURCE_REGISTRY,
            status_callback=lambda message: console.print(f"[yellow]{message}[/yellow]"),
        )
        console.print(
            "[green]Podcast/RSS watcher started.[/green] "
            f"Podcast Index recent-data interval: {interval}s | direct RSS interval: "
            f"{PODCAST_RSS_INTERVAL_SECONDS}s"
        )
        console.print(
            "Podcast Index watches newly indexed episodes globally. Relevant Claude feeds "
            "are also discovered and monitored directly by RSS. Referral-containing show "
            "notes go through the shared validator/dedupe/Telegram pipeline."
        )

        async for post in watcher.stream():
            console.print(
                f"[cyan]Podcast referral match:[/cyan] {post.source} "
                f"{post.created_at.isoformat() if post.created_at else ''}"
            )
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Podcast/RSS watcher stopped.[/yellow]")


@cli.command("x-test")
def x_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured X search query",
    ),
) -> None:
    async def run() -> None:
        if not X_BEARER_TOKEN:
            console.print("[red]Set X_BEARER_TOKEN in .env first.[/red]")
            raise typer.Exit(1)

        queries = [query] if query else X_QUERIES
        watcher = XWatcher(
            X_BEARER_TOKEN,
            queries,
            interval_seconds=X_WATCH_INTERVAL_SECONDS,
            max_results=X_SEARCH_LIMIT,
        )

        try:
            posts = await watcher.fetch()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            console.print(
                f"[red]X API returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            if exc.response.status_code in {402, 403} and "credit" in detail.lower():
                console.print(
                    "[yellow]The app is configured, but X API credits are required "
                    "for this request.[/yellow]"
                )
            raise typer.Exit(1)
        except httpx.HTTPError as exc:
            console.print(f"[red]X request failed:[/red] {exc}")
            raise typer.Exit(1)

        table = Table("Source", "Posted", "URL", "Referral in text?")
        for post in posts:
            table.add_row(
                post.source,
                post.created_at.isoformat() if post.created_at else "-",
                post.url or "-",
                "yes" if extract_referral_links(post.text) else "no",
            )
        console.print(table)
        console.print(f"[green]X API working. {len(posts)} recent posts returned.[/green]")

    asyncio.run(run())


@cli.command("x-stream-setup")
def x_stream_setup() -> None:
    if not X_BEARER_TOKEN:
        console.print("[red]Set X_BEARER_TOKEN in .env first.[/red]")
        raise typer.Exit(1)

    async def run() -> None:
        watcher = XFilteredStreamWatcher(
            X_BEARER_TOKEN,
            X_STREAM_RULES,
            reconnect_seconds=X_STREAM_RECONNECT_SECONDS,
        )
        try:
            rules = await watcher.sync_rules()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            console.print(
                f"[red]X stream rule setup returned HTTP {exc.response.status_code}.[/red] {detail}"
            )
            raise typer.Exit(1)

        table = Table("Rule ID", "Tag", "Rule")
        for rule in rules:
            table.add_row(
                str(rule.get("id") or "-"),
                str(rule.get("tag") or "-"),
                str(rule.get("value") or "-"),
            )
        console.print(table)
        console.print("[green]X Filtered Stream rules are ready.[/green]")

    asyncio.run(run())


@cli.command("x-stream-rules")
def x_stream_rules() -> None:
    if not X_BEARER_TOKEN:
        console.print("[red]Set X_BEARER_TOKEN in .env first.[/red]")
        raise typer.Exit(1)

    async def run() -> None:
        watcher = XFilteredStreamWatcher(X_BEARER_TOKEN, X_STREAM_RULES)
        rules = await watcher.list_rules()
        table = Table("Rule ID", "Tag", "Rule")
        for rule in rules:
            table.add_row(
                str(rule.get("id") or "-"),
                str(rule.get("tag") or "-"),
                str(rule.get("value") or "-"),
            )
        console.print(table)
        if not rules:
            console.print("[yellow]No X stream rules configured yet.[/yellow]")

    try:
        asyncio.run(run())
    except httpx.HTTPStatusError as exc:
        console.print(
            f"[red]X API returned HTTP {exc.response.status_code}.[/red] "
            f"{exc.response.text[:500]}"
        )
        raise typer.Exit(1)


@cli.command("watch-x")
def watch_x() -> None:
    if not X_BEARER_TOKEN or not X_STREAM_RULES:
        console.print(
            "[red]X is not configured. Set X_BEARER_TOKEN and X_STREAM_RULES in .env.[/red]"
        )
        raise typer.Exit(1)

    async def run() -> None:
        watcher = XFilteredStreamWatcher(
            X_BEARER_TOKEN,
            X_STREAM_RULES,
            reconnect_seconds=X_STREAM_RECONNECT_SECONDS,
        )
        await watcher.sync_rules()
        console.print("[green]X live Filtered Stream connected.[/green]")
        console.print(f"Rules: {' | '.join(X_STREAM_RULES)}")
        console.print(
            "Waiting for new matching X posts. Valid referrals are validated, "
            "deduplicated, stored, and sent to Telegram."
        )

        async for post in watcher.stream():
            console.print(
                f"[cyan]X match:[/cyan] {post.source} "
                f"{post.created_at.isoformat() if post.created_at else ''}"
            )
            results = await process_post(post)
            _print_results(results)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]X live watcher stopped.[/yellow]")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        console.print(
            f"[red]X live stream stopped with HTTP {exc.response.status_code}.[/red] {detail}"
        )
        raise typer.Exit(1)


@cli.command("recent")
def recent(limit: int = typer.Option(20, min=1, max=200)) -> None:
    async def run() -> None:
        await init_db()
        rows = await get_recent_referrals(limit)
        table = Table("Code", "Status", "Source", "Detected")
        for row in rows:
            table.add_row(
                row["referral_code"],
                row["status"],
                row["source"],
                row["detected_at"],
            )
        console.print(table)

    asyncio.run(run())


@cli.command("telegram-test")
def telegram_test() -> None:
    async def run() -> None:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
            console.print(
                "[red]Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS in .env first.[/red]"
            )
            raise typer.Exit(1)
        successful, failures = await send_telegram_message_all(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_IDS,
            "Referral Monitor test: Telegram alerts are working.",
        )
        console.print(
            f"[green]Telegram test sent to {len(successful)}/{len(TELEGRAM_CHAT_IDS)} configured chat(s).[/green]"
        )
        if failures:
            console.print(f"[yellow]Failed chats: {', '.join(failures)}[/yellow]")

    asyncio.run(run())


@cli.command("telegram-chats")
def telegram_chats() -> None:
    async def run() -> None:
        if not TELEGRAM_BOT_TOKEN:
            console.print("[red]Set TELEGRAM_BOT_TOKEN in .env first.[/red]")
            raise typer.Exit(1)

        updates = await get_telegram_updates(TELEGRAM_BOT_TOKEN)
        found: dict[str, str] = {}
        for update in updates:
            message = update.get("message") or update.get("channel_post") or {}
            chat = message.get("chat") or {}
            if "id" in chat:
                label = (
                    chat.get("title")
                    or chat.get("username")
                    or chat.get("first_name")
                    or "chat"
                )
                found[str(chat["id"])] = str(label)

        if not found:
            console.print(
                "[yellow]No chats found. Send the bot a message, then run this again.[/yellow]"
            )
            return

        table = Table("Chat ID", "Name")
        for chat_id, name in found.items():
            table.add_row(chat_id, name)
        console.print(table)

    asyncio.run(run())


@cli.command("telegram-status")
def telegram_status() -> None:
    """Send a concise configuration snapshot to every Telegram destination."""

    async def run() -> None:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
            console.print(
                "[red]Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS in .env first.[/red]"
            )
            raise typer.Exit(1)

        lines = [
            "Referral Monitor status",
            f"X live stream: {'ready' if X_BEARER_TOKEN else 'not configured'}",
            f"YouTube monitor: {'ready' if YOUTUBE_API_KEY else 'not configured'} ({YOUTUBE_WATCH_INTERVAL_SECONDS}s)",
            f"Web / Exa monitor: {'ready' if EXA_API_KEY else 'not configured'} ({EXA_WATCH_INTERVAL_SECONDS}s)",
            f"Podcast / RSS monitor: {'ready' if _podcast_configured() else 'not configured'} ({PODCAST_INDEX_WATCH_INTERVAL_SECONDS}s)",
            f"Validator browser fallback: {'ready' if VALIDATOR_BROWSER_FALLBACK_ENABLED else 'off'}",
            f"Reddit: {'ready' if _reddit_configured() else 'pending / not configured'}",
            f"Threads: {'ready' if _threads_configured() else 'pending / not configured'}",
            f"Telegram destinations: {len(TELEGRAM_CHAT_IDS)}",
            f"Status generated: {datetime.now(timezone.utc).isoformat()}",
        ]
        successful, failures = await send_telegram_message_all(
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS, "\n".join(lines)
        )
        console.print(
            f"[green]Status sent to {len(successful)}/{len(TELEGRAM_CHAT_IDS)} configured chat(s).[/green]"
        )
        if failures:
            console.print(f"[yellow]Failed chats: {', '.join(failures)}[/yellow]")

    asyncio.run(run())


@cli.command("status")
def status() -> None:
    table = Table("Component", "Configured")
    table.add_row(
        "Telegram",
        f"yes ({len(TELEGRAM_CHAT_IDS)} chat(s))" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS else "no",
    )
    table.add_row(
        "Validator browser fallback",
        "yes" if VALIDATOR_BROWSER_FALLBACK_ENABLED else "no",
    )
    if VALIDATOR_BROWSER_FALLBACK_ENABLED:
        table.add_row(
            "Validator browser",
            f"{VALIDATOR_BROWSER_CHANNEL or 'chromium'} ({'headless' if VALIDATOR_BROWSER_HEADLESS else 'visible'})",
        )
    table.add_row("Threads", "yes" if _threads_configured() else "no")
    table.add_row("Threads OAuth app", "yes" if _threads_oauth_configured() else "no")
    table.add_row(
        "Threads token source",
        ".env" if THREADS_ACCESS_TOKEN else (THREADS_TOKEN_FILE if _threads_token_record() else "-")
    )
    table.add_row("Threads interval", f"{THREADS_WATCH_INTERVAL_SECONDS}s")
    table.add_row("Threads queries", ", ".join(THREADS_QUERIES) or "-")
    table.add_row("Reddit", "yes" if _reddit_configured() else "no")
    table.add_row("Reddit interval", f"{REDDIT_WATCH_INTERVAL_SECONDS}s")
    table.add_row("Reddit queries", " | ".join(REDDIT_QUERIES) or "-")
    table.add_row("YouTube", "yes" if _youtube_configured() else "no")
    table.add_row("YouTube interval", f"{YOUTUBE_WATCH_INTERVAL_SECONDS}s")
    table.add_row("YouTube queries", " | ".join(YOUTUBE_QUERIES) or "-")
    table.add_row("Web / Exa", "yes" if _web_configured() else "no")
    table.add_row("Web interval", f"{EXA_WATCH_INTERVAL_SECONDS}s")
    table.add_row("Web search type", EXA_SEARCH_TYPE)
    table.add_row("Web queries", " | ".join(EXA_QUERIES) or "-")
    table.add_row("Podcast / RSS", "yes" if _podcast_configured() else "no")
    table.add_row("Podcast Index interval", f"{PODCAST_INDEX_WATCH_INTERVAL_SECONDS}s")
    table.add_row("Podcast RSS interval", f"{PODCAST_RSS_INTERVAL_SECONDS}s")
    table.add_row("Podcast discovery queries", " | ".join(PODCAST_DISCOVERY_QUERIES) or "-")
    table.add_row("X", "yes" if _x_configured() else "no")
    table.add_row("X mode", "Filtered Stream (live)")
    table.add_row("X recent query", " | ".join(X_QUERIES) or "-")
    table.add_row("X stream rules", " | ".join(X_STREAM_RULES) or "-")
    console.print(table)


@cli.command("init-db")
def init_database() -> None:
    asyncio.run(init_db())
    console.print("[green]Database ready.[/green]")


if __name__ == "__main__":
    cli()
