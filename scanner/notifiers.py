import html
import json
import logging
import smtplib
import email.utils
import requests
from email.message import EmailMessage

from .utils import TIMEOUT


class EmailNotifier:
    def __init__(self, server: str, username: str, password: str, to_addrs):
        self.server = server
        self.username = username
        self.password = password
        self.to_addrs = to_addrs

    def send(self, title: str, price: str, url: str, image: str):
        try:
            msg = EmailMessage()
            msg["To"] = self.to_addrs
            msg["From"] = email.utils.formataddr(("Subito Scanner", self.username))
            msg["Subject"] = "Subito Scanner - New Item"
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg["Message-ID"] = email.utils.make_msgid()
            msg.set_content(f"{title}\n{price}\n🔗 {url}\n📷 {image}")
            with smtplib.SMTP(self.server, 587) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self.username, self.password)
                smtp.send_message(msg)
            logging.info("e-mail sent")
        except smtplib.SMTPException as e:
            logging.error(f"smtp error: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"error sending e-mail: {e}", exc_info=True)


class SlackNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, title: str, price: str, url: str, image: str):
        message = f"*{title}*\n🏷️ {price}\n🔗 {url}\n📷 {image}"
        try:
            response = requests.post(
                self.webhook_url,
                data=json.dumps({"text": message}),
                headers={"Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                logging.error(f"slack notification failed: {response.status_code}, {response.text}")
            else:
                logging.info("slack notification sent")
        except requests.exceptions.RequestException as e:
            logging.error(f"error sending slack message: {e}")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{token}"

    def send(self, title: str, price: str, url: str, image: str):
        safe_title = html.escape(title)
        safe_price = html.escape(str(price))
        caption = (
            f"🆕 <b>new on subito</b>\n\n"
            f"<b>{safe_title}</b>\n"
            f"💰 {safe_price}\n"
            f"🔗 {url}"
        )
        if image:
            self._post_photo(self.chat_id, image, caption)
        else:
            self._post_message(self.chat_id, caption, disable_preview=True)

    def reply(self, chat_id, text: str):
        self._post_message(chat_id, text)

    def get_updates(self, offset: int, timeout: int = 1) -> list:
        try:
            response = requests.get(
                f"{self._base_url}/getUpdates",
                params={"offset": offset, "timeout": timeout},
                # http timeout must be longer than the long-poll timeout
                timeout=timeout + 10,
            )
            data = response.json()
            if response.status_code == 200 and data.get("ok"):
                return data.get("result", [])
        except requests.exceptions.RequestException as e:
            logging.error(f"error fetching telegram updates: {e}")
        return []

    def register_commands(self, commands: list):
        try:
            response = requests.post(
                f"{self._base_url}/setMyCommands",
                json={"commands": commands},
                timeout=TIMEOUT,
            )
            if response.status_code == 200:
                logging.info("bot commands registered with telegram")
            else:
                logging.error(f"failed to register bot commands: {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"error registering bot commands: {e}")

    def set_descriptions(self, short: str, description: str):
        """set the bot profile short description and about text."""
        for method, payload in (
            ("setMyShortDescription", {"short_description": short}),
            ("setMyDescription", {"description": description}),
        ):
            try:
                response = requests.post(
                    f"{self._base_url}/{method}",
                    json=payload,
                    timeout=TIMEOUT,
                )
                if response.status_code != 200 or not response.json().get("ok"):
                    logging.error(f"failed {method}: {response.text}")
            except requests.exceptions.RequestException as e:
                logging.error(f"error calling {method}: {e}")

    def _post_photo(self, chat_id, photo_url: str, caption: str):
        try:
            response = requests.post(
                f"{self._base_url}/sendPhoto",
                params={
                    "chat_id": chat_id,
                    "photo": photo_url,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                logging.error(f"telegram photo failed: {response.status_code}, {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"error sending telegram photo: {e}")

    def _post_message(self, chat_id, text: str, disable_preview: bool = False):
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if disable_preview:
            params["link_preview_options"] = json.dumps({"is_disabled": True})
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                params=params,
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                logging.error(f"telegram message failed: {response.status_code}, {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"error sending telegram message: {e}")
