import html
import json
import logging
import smtplib
import email.utils
import requests
from email.message import EmailMessage

from .constants import TIMEOUT

# telegram photo captions max 1024 chars; keep body short so title/price/url fit
_TELEGRAM_BODY_MAX = 400


def _shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


class EmailNotifier:
    def __init__(self, server: str, username: str, password: str, to_addrs):
        self.server = server
        self.username = username
        self.password = password
        self.to_addrs = to_addrs

    def send(
        self,
        title: str,
        price: str,
        url: str,
        image: str,
        description: str = "",
        place: str = "",
        posted: str = "",
        seller: str = "",
    ):
        try:
            msg = EmailMessage()
            msg["To"] = self.to_addrs
            msg["From"] = email.utils.formataddr(("Subito Scanner", self.username))
            msg["Subject"] = "Subito Scanner - New Item"
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg["Message-ID"] = email.utils.make_msgid()
            body_lines = [title]
            if description:
                body_lines.append(description)
            if seller:
                body_lines.append(f"👤 {seller}")
            if place:
                body_lines.append(f"📍 {place}")
            if posted:
                body_lines.append(f"📅 {posted}")
            body_lines.extend([str(price), f"🔗 {url}"])
            if image:
                body_lines.append(f"📷 {image}")
            msg.set_content("\n".join(body_lines))
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

    def send(
        self,
        title: str,
        price: str,
        url: str,
        image: str,
        description: str = "",
        place: str = "",
        posted: str = "",
        seller: str = "",
    ):
        message_lines = [f"*{title}*"]
        if description:
            message_lines.append(_shorten(description, 500))
        if seller:
            message_lines.append(f"👤 {seller}")
        if place:
            message_lines.append(f"📍 {place}")
        if posted:
            message_lines.append(f"📅 {posted}")
        message_lines.extend([f"🏷️ {price}", f"🔗 {url}"])
        if image:
            message_lines.append(f"📷 {image}")
        message = "\n".join(message_lines)
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

    def send(
        self,
        title: str,
        price: str,
        url: str,
        image: str,
        description: str = "",
        place: str = "",
        posted: str = "",
        seller: str = "",
    ):
        safe_title = html.escape(title)
        safe_price = html.escape(str(price))
        desc_block = ""
        if description:
            desc_block = f"\n{html.escape(_shorten(description, _TELEGRAM_BODY_MAX))}\n"
        meta_bits = []
        if seller:
            meta_bits.append(f"👤 {html.escape(seller)}")
        if place:
            meta_bits.append(f"📍 {html.escape(place)}")
        if posted:
            meta_bits.append(f"📅 {html.escape(posted)}")
        meta = ("\n".join(meta_bits) + "\n") if meta_bits else ""
        caption = (
            f"🆕 <b>New on Subito</b>\n\n"
            f"<b>{safe_title}</b>"
            f"{desc_block}"
            f"\n{meta}"
            f"💰 {safe_price}\n"
            f"🔗 {url}"
        )
        # photo captions are capped at 1024; fall back to text if still too long
        if image and len(caption) <= 1024:
            self._post_photo(self.chat_id, image, caption)
        else:
            self._post_message(self.chat_id, caption, disable_preview=True)

    def reply(self, chat_id, text: str, reply_markup=None, disable_preview: bool = False):
        self._post_message(chat_id, text, reply_markup=reply_markup, disable_preview=disable_preview)

    def edit_message(self, chat_id, message_id: int, text: str, reply_markup=None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            response = requests.post(
                f"{self._base_url}/editMessageText",
                json=payload,
                timeout=TIMEOUT,
            )
            if response.status_code != 200 or not response.json().get("ok"):
                # message unchanged is fine; otherwise fall back to a new reply
                desc = (response.json() or {}).get("description", "")
                if "message is not modified" not in desc.lower():
                    logging.error(f"telegram edit failed: {response.text}")
                    self.reply(chat_id, text, reply_markup=reply_markup)
        except requests.exceptions.RequestException as e:
            logging.error(f"error editing telegram message: {e}")

    def answer_callback(self, callback_query_id: str, text: str = None, show_alert: bool = False):
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        try:
            requests.post(
                f"{self._base_url}/answerCallbackQuery",
                json=payload,
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            logging.error(f"error answering callback: {e}")

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

    def _post_message(self, chat_id, text: str, reply_markup=None, disable_preview: bool = False):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if disable_preview:
            payload["link_preview_options"] = {"is_disabled": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json=payload,
                timeout=TIMEOUT,
            )
            if response.status_code != 200:
                logging.error(f"telegram message failed: {response.status_code}, {response.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"error sending telegram message: {e}")
