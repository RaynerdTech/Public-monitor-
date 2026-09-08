import asyncio
from datetime import datetime, timezone

import httpx
import typer
from rich.console import Console
from rich.table import Table

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    THREADS_ACCESS_TOKEN,
    THREADS_QUERIES,
    THREADS_SEARCH_LIMIT,
    THREADS_WATCH_INTERVAL_SECONDS,
    URL_WATCH_INTERVAL_SECONDS,
    X_BEARER_TOKEN,
    X_QUERIES,
    X_SEARCH_LIMIT,
    X_STREAM_RECONNECT_SECONDS,
    X_STREAM_RULES,
    X_WATCH_INTERVAL_SECONDS,
)
from app.core.database import get_recent_referrals, init_db
from app.core.extractor import extract_referral_links
from app.services.pipeline import process_post
from app.services.telegram import (
    format_referral_alert,
    get_telegram_updates,
    send_telegram_message,
)
from app.services.validator import ValidationResult
from app.watchers.base import SourcePost
from app.watchers.file import FileWatcher
from app.watchers.threads import ThreadsWatcher
from app.watchers.url import UrlWatcher
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


def _threads_configured() -> bool:
    return bool(THREADS_ACCESS_TOKEN and THREADS_QUERIES)


def _x_configured() -> bool:
    return bool(X_BEARER_TOKEN and X_QUERIES)


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
            if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
                console.print("[red]Telegram is not configured in .env.[/red]")
                raise typer.Exit(1)
            candidate, validation, _ = results[0]
            alert = format_referral_alert(candidate, validation, simulated=True)
            await send_telegram_message(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, alert)
            console.print("[green]Telegram test alert sent.[/green]")

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


@cli.command("threads-test")
def threads_test(
    query: str | None = typer.Option(
        None,
        help="Override the configured Threads search query",
    ),
) -> None:
    async def run() -> None:
        if not THREADS_ACCESS_TOKEN:
            console.print("[red]Set THREADS_ACCESS_TOKEN in .env first.[/red]")
            raise typer.Exit(1)

        queries = [query] if query else THREADS_QUERIES
        watcher = ThreadsWatcher(
            THREADS_ACCESS_TOKEN,
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
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            console.print(
                "[red]Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.[/red]"
            )
            raise typer.Exit(1)
        await send_telegram_message(
            TELEGRAM_BOT_TOKEN,
            TELEGRAM_CHAT_ID,
            "Referral Monitor test: Telegram alerts are working.",
        )
        console.print("[green]Telegram test sent.[/green]")

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


@cli.command("status")
def status() -> None:
    table = Table("Component", "Configured")
    table.add_row(
        "Telegram",
        "yes" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "no",
    )
    table.add_row("Threads", "yes" if _threads_configured() else "no")
    table.add_row("Threads interval", f"{THREADS_WATCH_INTERVAL_SECONDS}s")
    table.add_row("Threads queries", ", ".join(THREADS_QUERIES) or "-")
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
