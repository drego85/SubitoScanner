#!/usr/bin/env python3
import argparse
import logging
from logging.handlers import RotatingFileHandler

import Config
from scanner.state import State
from scanner.notifiers import EmailNotifier, SlackNotifier, TelegramNotifier
from scanner.bot import TelegramBot
from scanner.core import SubitoScanner

# keep at most ~6 mb of logs on disk (3 x 2 mb rotated files)
LOG_FILE = "subito_scanner.log"
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 3


def setup_logging():
    """size-based rotation: when the active log hits LOG_MAX_BYTES it is
    renamed to .1, .2, ... and the oldest backup beyond LOG_BACKUP_COUNT is deleted.
    """
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    logging.basicConfig(
        handlers=[handler],
        format="%(asctime)s - %(filename)s - %(funcName)10s():%(lineno)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        force=True,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Subito Scanner - Automated search with notifications.")
    parser.add_argument("--dry-run",     action="store_true", help="run in test mode (no notifications will be sent).")
    parser.add_argument("--bot-service", action="store_true", help="run persistent real-time bot listener (managed by systemd).")
    parser.add_argument("--scan-only",   action="store_true", help="run the scanner without bot polling (use when bot-service is running).")
    return parser.parse_args()


def build_notifiers(telegram: TelegramNotifier) -> list:
    """assemble the active notifier list from Config."""
    notifiers = []
    if telegram:
        notifiers.append(telegram)
    if Config.smtp_username and Config.smtp_server:
        notifiers.append(EmailNotifier(Config.smtp_server, Config.smtp_username, Config.smtp_psw, Config.smtp_toaddrs))
    if Config.slack_webhook_url:
        notifiers.append(SlackNotifier(Config.slack_webhook_url))
    return notifiers


def main():
    setup_logging()
    args = parse_args()

    # seed queries from Config on first run; after that state.queries is the source of truth
    state = State.load(seed_queries=Config.queries)

    telegram = None
    if Config.telegram_bot_token and Config.telegram_chat_id:
        telegram = TelegramNotifier(Config.telegram_bot_token, Config.telegram_chat_id)
        notifiers = build_notifiers(telegram)
        bot = TelegramBot(telegram, state, notifiers=notifiers)

        if args.bot_service:
            bot.run_forever()  # blocks forever, managed by systemd
            return

        if not args.scan_only:
            bot.poll()  # single poll pass when running as cron without bot-service

        SubitoScanner(state, notifiers).run(dry_run=args.dry_run)
    else:
        SubitoScanner(state, build_notifiers(None)).run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
