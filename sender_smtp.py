from __future__ import annotations

import base64
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable, Optional

import spnego


class SMTPTransportError(RuntimeError):
    pass


class SMTPAuthenticationError(SMTPTransportError):
    pass


@dataclass(frozen=True)
class SMTPConfig:
    server: str = "mail-server.mephi.ru"
    port: int = 587
    domain: str = "MEPHI"
    timeout: int = 30


class MephiSMTPSender:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        from_email: str,
        config: SMTPConfig | None = None,
        debug: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.from_email = from_email
        self.config = config or SMTPConfig()
        self.debug = debug
        self._smtp: Optional[smtplib.SMTP] = None

    @property
    def ntlm_username(self) -> str:
        if "\\" in self.username:
            return self.username
        return f"{self.config.domain}\\{self.username}"

    def connect(self) -> None:
        if self._smtp is not None:
            return

        smtp = smtplib.SMTP(
            self.config.server,
            self.config.port,
            timeout=self.config.timeout,
        )
        if self.debug:
            smtp.set_debuglevel(1)

        try:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            self._smtp = smtp
            self._authenticate_ntlm()
        except Exception:
            try:
                smtp.close()
            finally:
                self._smtp = None
            raise

    def _authenticate_ntlm(self) -> None:
        if self._smtp is None:
            raise SMTPTransportError("SMTP connection is not open.")

        ntlm = spnego.client(
            self.ntlm_username,
            self.password,
            protocol="ntlm",
        )

        code, response = self._smtp.docmd("AUTH", "NTLM")
        if code != 334:
            raise SMTPAuthenticationError(
                f"AUTH NTLM rejected: {code} {self._decode(response)}"
            )

        negotiate = ntlm.step()
        if negotiate is None:
            raise SMTPAuthenticationError("Could not create NTLM negotiate message.")

        code, response = self._smtp.docmd(
            base64.b64encode(negotiate).decode("ascii")
        )
        if code != 334:
            raise SMTPAuthenticationError(
                f"NTLM negotiate failed: {code} {self._decode(response)}"
            )

        challenge = base64.b64decode(response.strip(), validate=True)
        authenticate = ntlm.step(challenge)
        if authenticate is None:
            raise SMTPAuthenticationError("Could not create NTLM authenticate message.")

        code, response = self._smtp.docmd(
            base64.b64encode(authenticate).decode("ascii")
        )
        if code != 235:
            raise SMTPAuthenticationError(
                f"NTLM authentication failed: {code} {self._decode(response)}"
            )

    def send(
        self,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
    ) -> dict[str, tuple[int, bytes]]:
        if self._smtp is None:
            self.connect()

        recipients = self._unique_addresses(envelope_recipients)
        if not recipients:
            raise SMTPTransportError("No envelope recipients specified.")

        assert self._smtp is not None
        return self._smtp.send_message(
            message,
            from_addr=self.from_email,
            to_addrs=recipients,
        )

    def close(self) -> None:
        smtp = self._smtp
        self._smtp = None
        if smtp is None:
            return

        try:
            smtp.quit()
        except Exception:
            try:
                smtp.close()
            except Exception:
                pass

    def __enter__(self) -> "MephiSMTPSender":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _decode(value: bytes | str) -> str:
        return value.decode(errors="replace") if isinstance(value, bytes) else str(value)

    @staticmethod
    def _unique_addresses(addresses: Iterable[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for address in addresses:
            clean = address.strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
        return result
