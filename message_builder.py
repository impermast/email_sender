from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Sequence


@dataclass
class Recipient:
    name: str
    email: str
    title: str = ""
    flag: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    attachments: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return " ".join(
            part for part in (self.title.strip(), self.name.strip()) if part
        )


@dataclass(frozen=True)
class GlobalMessageConfig:
    from_email: str
    global_subject: str
    global_message: str
    global_cc: Sequence[str] = ()
    global_bcc: Sequence[str] = ()
    global_attachments: Sequence[str] = ()
    attachments_dir: str = "attachments"


@dataclass
class PreparedMessage:
    message: EmailMessage
    envelope_recipients: list[str]
    resolved_attachments: list[Path]


class TemplateError(ValueError):
    pass


class SimpleTemplateRenderer:
    _pattern = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

    def render(self, template: str, recipient: Recipient) -> str:
        def replace(match: re.Match[str]) -> str:
            expression = match.group(1).strip()

            fixed = {
                "recipient.name": recipient.name,
                "recipient.email": recipient.email,
                "recipient.title": recipient.title,
                "recipient.flag": recipient.flag,
                "recipient.display_name": recipient.display_name,
            }
            if expression in fixed:
                return str(fixed[expression])

            prefix = "recipient.options."
            if expression.startswith(prefix):
                key = expression[len(prefix):]
                if not key:
                    raise TemplateError("Empty recipient.options key.")
                if key not in recipient.options:
                    raise TemplateError(f"Missing recipient option: {key}")
                return str(recipient.options[key])

            raise TemplateError(f"Unsupported template expression: {expression}")

        return self._pattern.sub(replace, template)


class MessageBuilder:
    def __init__(
        self,
        config: GlobalMessageConfig,
        renderer: SimpleTemplateRenderer | None = None,
    ) -> None:
        self.config = config
        self.renderer = renderer or SimpleTemplateRenderer()

    def build(self, recipient: Recipient) -> PreparedMessage:
        if not recipient.email.strip():
            raise ValueError(f"Recipient {recipient.name!r} has no email.")

        subject = self.renderer.render(self.config.global_subject, recipient)
        body = self.renderer.render(self.config.global_message, recipient)

        cc = self._unique([*self.config.global_cc, *recipient.cc])
        bcc = self._unique([*self.config.global_bcc, *recipient.bcc])

        msg = EmailMessage()
        msg["From"] = self.config.from_email
        msg["To"] = recipient.email.strip()
        if cc:
            msg["Cc"] = ", ".join(cc)

        # BCC is intentionally not written into headers.
        msg["Subject"] = subject
        msg.set_content(body)

        attachment_paths = self._resolve_attachments(recipient)
        for path in attachment_paths:
            self._attach_file(msg, path)

        envelope = self._unique([recipient.email, *cc, *bcc])

        return PreparedMessage(
            message=msg,
            envelope_recipients=envelope,
            resolved_attachments=attachment_paths,
        )

    def _resolve_attachments(self, recipient: Recipient) -> list[Path]:
        names = [*self.config.global_attachments, *recipient.attachments]
        root = Path(self.config.attachments_dir)

        result: list[Path] = []
        seen: set[Path] = set()

        for name in names:
            raw = str(name).strip()
            if not raw:
                continue

            path = Path(raw)
            if not path.is_absolute():
                path = root / path

            path = path.expanduser().resolve()

            if path in seen:
                continue
            if not path.is_file():
                raise FileNotFoundError(f"Attachment not found: {path}")

            seen.add(path)
            result.append(path)

        return result

    @staticmethod
    def _attach_file(msg: EmailMessage, path: Path) -> None:
        mime_type, encoding = mimetypes.guess_type(path.name)
        if mime_type is None or encoding is not None:
            maintype, subtype = "application", "octet-stream"
        else:
            maintype, subtype = mime_type.split("/", 1)

        with path.open("rb") as handle:
            msg.add_attachment(
                handle.read(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

    @staticmethod
    def _unique(values: Sequence[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()

        for value in values:
            clean = str(value).strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)

        return result
