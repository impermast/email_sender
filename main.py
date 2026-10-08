from __future__ import annotations

import argparse
import copy
import csv
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Protocol

import yaml

from message_builder import (
    GlobalMessageConfig,
    MessageBuilder,
    PreparedMessage,
    Recipient,
)
from sender_smtp import MephiSMTPSender
from sender_ews import MephiEWSSender


CONFIG_FILE = Path("mailing.yaml")
PASSWORD_FILE = Path("pass.txt")
ATTACHMENTS_DIR = "attachments"

LOGS_DIR = Path("logs")
SEND_LOG_FILE = LOGS_DIR / "send_log.csv"
TEST_LOG_FILE = LOGS_DIR / "test_log.csv"

LOG_COLUMNS = [
    "timestamp",
    "mailing_id",
    "recipient_name",
    "recipient_email",
    "transport",
    "status",
    "details",
]


@dataclass
class SendResult:
    status: str
    success: bool
    message: str
    errors: list[str]


class MailTransport(Protocol):
    def connect(self) -> None:
        ...

    def send(self, prepared: PreparedMessage) -> SendResult:
        ...

    def close(self) -> None:
        ...


class SMTPTransport:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        from_email: str,
    ) -> None:
        self.sender = MephiSMTPSender(
            username=username,
            password=password,
            from_email=from_email,
        )

    def connect(self) -> None:
        self.sender.connect()

    def send(
        self,
        prepared: PreparedMessage,
    ) -> SendResult:
        refused = self.sender.send(
            prepared.message,
            prepared.envelope_recipients,
        )

        if not refused:
            return SendResult(
                status="SUCCESS",
                success=True,
                message="Message accepted for delivery.",
                errors=[],
            )

        errors = []

        for email, error in refused.items():
            code, server_message = error

            if isinstance(server_message, bytes):
                server_message = server_message.decode(
                    errors="replace"
                )

            errors.append(
                f"{email}: {code} {server_message}"
            )

        refused_addresses = {
            email.lower()
            for email in refused
        }

        main_recipient = (
            prepared.envelope_recipients[0]
            .strip()
            .lower()
        )

        if main_recipient in refused_addresses:
            return SendResult(
                status="FAILED",
                success=False,
                message="Main recipient was refused.",
                errors=errors,
            )

        return SendResult(
            status="PARTIAL",
            success=True,
            message=(
                "Main recipient accepted, but some "
                "CC/BCC recipients were refused."
            ),
            errors=errors,
        )

    def close(self) -> None:
        self.sender.close()


class EWSTransport:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        from_email: str,
    ) -> None:
        self.sender = MephiEWSSender(
            username=username,
            password=password,
            from_email=from_email,
        )

    def connect(self) -> None:
        self.sender.connect()

    def send(
        self,
        prepared: PreparedMessage,
    ) -> SendResult:
        self.sender.send(
            prepared.message,
            prepared.envelope_recipients,
            prepared.resolved_attachments,
        )

        return SendResult(
            status="SUCCESS",
            success=True,
            message=(
                "Message sent via EWS and "
                "saved to Sent Items."
            ),
            errors=[],
        )

    def close(self) -> None:
        self.sender.close()


@dataclass
class AppConfig:
    mailing_id: str
    transport_type: str
    username: str
    from_email: str
    message_config: GlobalMessageConfig
    recipients: list[Recipient]
    delay_seconds: float


def load_yaml() -> dict:
    if not CONFIG_FILE.is_file():
        raise FileNotFoundError(
            f"Config file not found: {CONFIG_FILE}"
        )

    with CONFIG_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = yaml.safe_load(file)

    if not isinstance(data, dict):
        raise ValueError(
            "mailing.yaml must contain a YAML object."
        )

    return data


def load_password() -> str:
    if not PASSWORD_FILE.is_file():
        raise FileNotFoundError(
            f"Password file not found: {PASSWORD_FILE}"
        )

    password = PASSWORD_FILE.read_text(
        encoding="utf-8"
    ).strip()

    if not password:
        raise ValueError("pass.txt is empty.")

    return password


def parse_transport(data: dict) -> str:
    transport = data.get(
        "transport",
        {"type": "smtp"},
    )

    if isinstance(transport, str):
        return transport.strip().lower()

    if isinstance(transport, dict):
        return str(
            transport.get("type", "smtp")
        ).strip().lower()

    raise ValueError(
        "transport must be a string or YAML object."
    )


def make_recipient(
    raw: dict,
    number: int,
) -> Recipient:
    if not isinstance(raw, dict):
        raise ValueError(
            f"Recipient #{number} must be "
            f"a YAML object."
        )

    options = raw.get("options") or {}

    if not isinstance(options, dict):
        raise ValueError(
            f"Recipient #{number}: options must "
            f"be a key-value object."
        )

    return Recipient(
        name=str(
            raw.get("name", "")
        ).strip(),
        email=str(
            raw.get("email", "")
        ).strip(),
        title=str(
            raw.get("title", "")
        ).strip(),
        flag=str(
            raw.get("flag", "")
        ),
        options=options,
        attachments=(
            raw.get("attachments") or []
        ),
        cc=raw.get("cc") or [],
        bcc=raw.get("bcc") or [],
    )


def load_config() -> AppConfig:
    data = load_yaml()

    mailing_id = str(
        data.get("mailing_id", "")
    ).strip()

    if not mailing_id:
        raise ValueError(
            "mailing_id is required."
        )

    account = data.get("account") or {}

    if not isinstance(account, dict):
        raise ValueError(
            "account must be a YAML object."
        )

    username = str(
        account.get("username", "")
    ).strip()

    from_email = str(
        account.get("email", "")
    ).strip()

    if not username:
        raise ValueError(
            "account.username is required."
        )

    if not from_email:
        raise ValueError(
            "account.email is required."
        )

    raw_recipients = (
        data.get("recipients") or []
    )

    if not isinstance(
        raw_recipients,
        list,
    ):
        raise ValueError(
            "recipients must be a list."
        )

    recipients = [
        make_recipient(raw, number)
        for number, raw in enumerate(
            raw_recipients,
            start=1,
        )
    ]

    message_config = GlobalMessageConfig(
        from_email=from_email,
        global_subject=str(
            data.get("global_subject", "")
        ),
        global_message=str(
            data.get("global_message", "")
        ),
        global_cc=(
            data.get("global_cc") or []
        ),
        global_bcc=(
            data.get("global_bcc") or []
        ),
        global_attachments=(
            data.get("global_attachments") or []
        ),
        attachments_dir=ATTACHMENTS_DIR,
    )

    delay_seconds = float(
        data.get(
            "global_delay_seconds",
            2,
        )
    )

    if delay_seconds < 0:
        raise ValueError(
            "global_delay_seconds cannot "
            "be negative."
        )

    return AppConfig(
        mailing_id=mailing_id,
        transport_type=parse_transport(data),
        username=username,
        from_email=from_email,
        message_config=message_config,
        recipients=recipients,
        delay_seconds=delay_seconds,
    )


def valid_email(address: str) -> bool:
    _, parsed = parseaddr(address)

    if not parsed or "@" not in parsed:
        return False

    local, domain = parsed.rsplit("@", 1)

    return bool(
        local
        and domain
        and "." in domain
    )


def validate_config(
    config: AppConfig,
) -> None:
    if config.transport_type not in {
        "smtp",
        "ews",
    }:
        raise ValueError(
            "Unknown transport: "
            f"{config.transport_type}"
        )

    if not valid_email(
        config.from_email
    ):
        raise ValueError(
            "Invalid account email: "
            f"{config.from_email}"
        )

    if not config.recipients:
        raise ValueError(
            "No recipients configured."
        )

    seen = set()

    builder = MessageBuilder(
        config.message_config
    )

    for number, recipient in enumerate(
        config.recipients,
        start=1,
    ):
        if not recipient.name:
            raise ValueError(
                f"Recipient #{number} has no name."
            )

        if not valid_email(
            recipient.email
        ):
            raise ValueError(
                f"Recipient #{number} has "
                f"invalid email: "
                f"{recipient.email}"
            )

        key = recipient.email.lower()

        if key in seen:
            raise ValueError(
                "Duplicate recipient: "
                f"{recipient.email}"
            )

        seen.add(key)

        for address in [
            *recipient.cc,
            *recipient.bcc,
        ]:
            if not valid_email(
                str(address)
            ):
                raise ValueError(
                    f"Invalid email for "
                    f"{recipient.name}: "
                    f"{address}"
                )

        builder.build(recipient)

    for address in [
        *config.message_config.global_cc,
        *config.message_config.global_bcc,
    ]:
        if not valid_email(
            str(address)
        ):
            raise ValueError(
                f"Invalid global CC/BCC: "
                f"{address}"
            )


def create_transport(
    config: AppConfig,
    password: str,
) -> MailTransport:
    if config.transport_type == "smtp":
        return SMTPTransport(
            username=config.username,
            password=password,
            from_email=config.from_email,
        )

    if config.transport_type == "ews":
        return EWSTransport(
            username=config.username,
            password=password,
            from_email=config.from_email,
        )

    raise ValueError(
        f"Unknown transport: "
        f"{config.transport_type}"
    )


def ensure_logs_dir() -> None:
    LOGS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def append_log(
    *,
    file: Path,
    mailing_id: str,
    recipient_name: str,
    recipient_email: str,
    transport: str,
    status: str,
    details: str,
) -> None:
    ensure_logs_dir()

    new_file = not file.exists()

    with file.open(
        "a",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=LOG_COLUMNS,
        )

        if new_file:
            writer.writeheader()

        writer.writerow(
            {
                "timestamp": (
                    datetime.now()
                    .astimezone()
                    .isoformat(
                        timespec="seconds"
                    )
                ),
                "mailing_id": mailing_id,
                "recipient_name": (
                    recipient_name
                ),
                "recipient_email": (
                    recipient_email
                ),
                "transport": transport,
                "status": status,
                "details": details,
            }
        )


def load_send_log() -> list[dict]:
    if not SEND_LOG_FILE.exists():
        return []

    with SEND_LOG_FILE.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        return list(
            csv.DictReader(handle)
        )


def delivered_addresses(
    mailing_id: str,
) -> dict[str, str]:
    delivered = {}

    for row in load_send_log():
        if (
            row.get("mailing_id")
            != mailing_id
        ):
            continue

        status = row.get(
            "status",
            "",
        ).upper()

        if status not in {
            "SUCCESS",
            "PARTIAL",
        }:
            continue

        email = row.get(
            "recipient_email",
            "",
        ).strip().lower()

        if email:
            delivered[email] = status

    return delivered


def result_details(
    result: SendResult,
) -> str:
    if result.errors:
        return (
            result.message
            + " "
            + " | ".join(result.errors)
        )

    return result.message


def print_result(
    result: SendResult,
) -> None:
    print(
        f"{result.status}: "
        f"{result.message}"
    )

    for error in result.errors:
        print(f"  {error}")


def print_prepared_message(
    prepared: PreparedMessage,
    *,
    number: int,
    total: int,
) -> None:
    message = prepared.message

    print()
    print("=" * 60)
    print(
        f"MESSAGE {number}/{total}"
    )
    print("=" * 60)

    print(
        f"To:      "
        f"{message.get('To', '')}"
    )
    print(
        f"CC:      "
        f"{message.get('Cc', '')}"
    )
    print(
        f"Subject: "
        f"{message.get('Subject', '')}"
    )

    if prepared.resolved_attachments:
        print("Attachments:")

        for path in (
            prepared.resolved_attachments
        ):
            print(f"  {path.name}")
    else:
        print("Attachments: none")

    print()
    print(
        message.get_body(
            preferencelist=("plain",)
        ).get_content()
    )


def command_check() -> None:
    config = load_config()
    validate_config(config)

    print()
    print("=" * 60)
    print("CHECK SUCCESSFUL")
    print("=" * 60)
    print(
        f"Mailing ID:         "
        f"{config.mailing_id}"
    )
    print(
        f"Transport:          "
        f"{config.transport_type}"
    )
    print(
        f"Account:            "
        f"{config.from_email}"
    )
    print(
        f"Recipients:         "
        f"{len(config.recipients)}"
    )
    print(
        f"Global CC:          "
        f"{len(config.message_config.global_cc)}"
    )
    print(
        f"Global BCC:         "
        f"{len(config.message_config.global_bcc)}"
    )
    print(
        f"Global attachments: "
        f"{len(config.message_config.global_attachments)}"
    )
    print(
        f"Delay:              "
        f"{config.delay_seconds}s"
    )
    print()
    print("Configuration is valid.")
    print("=" * 60)


def command_status() -> None:
    config = load_config()
    validate_config(config)

    delivered = delivered_addresses(
        config.mailing_id
    )

    sent = 0
    pending = 0

    print()
    print("=" * 60)
    print("MAILING STATUS")
    print("=" * 60)
    print(
        f"Mailing ID: "
        f"{config.mailing_id}"
    )
    print()

    for number, recipient in enumerate(
        config.recipients,
        start=1,
    ):
        previous = delivered.get(
            recipient.email.lower()
        )

        if previous:
            sent += 1
            state = previous
        else:
            pending += 1
            state = "PENDING"

        print(
            f"{number:2}. "
            f"{state:8} "
            f"{recipient.display_name} "
            f"<{recipient.email}>"
        )

    print()
    print(f"Delivered: {sent}")
    print(f"Pending:   {pending}")
    print(
        f"Log:       {SEND_LOG_FILE}"
    )
    print("=" * 60)


def command_preview(
    recipient_number: int | None,
) -> None:
    config = load_config()
    validate_config(config)

    builder = MessageBuilder(
        config.message_config
    )

    if recipient_number is None:
        selected = list(
            enumerate(
                config.recipients,
                start=1,
            )
        )
    else:
        if (
            recipient_number < 1
            or recipient_number
            > len(config.recipients)
        ):
            raise ValueError(
                "Recipient number must "
                f"be between 1 and "
                f"{len(config.recipients)}."
            )

        selected = [
            (
                recipient_number,
                config.recipients[
                    recipient_number - 1
                ],
            )
        ]

    for number, recipient in selected:
        prepared = builder.build(
            recipient
        )

        print_prepared_message(
            prepared,
            number=number,
            total=len(
                config.recipients
            ),
        )


def command_test(
    test_email: str,
    recipient_number: int,
    test_cc: list[str] | None = None,
) -> None:
    test_cc = test_cc or []

    for address in test_cc:
        if not valid_email(address):
            raise ValueError(
                f"Invalid test CC email: {address}"
            )
    if not valid_email(test_email):
        raise ValueError(
            f"Invalid test email: "
            f"{test_email}"
        )

    config = load_config()
    validate_config(config)

    if (
        recipient_number < 1
        or recipient_number
        > len(config.recipients)
    ):
        raise ValueError(
            "Recipient number must "
            f"be between 1 and "
            f"{len(config.recipients)}."
        )

    recipient = config.recipients[
        recipient_number - 1
    ]

    builder = MessageBuilder(
        config.message_config
    )

    original = builder.build(
        recipient
    )

    test_message = copy.deepcopy(
        original.message
    )

    test_message.replace_header(
        "To",
        test_email,
    )

    if "Cc" in test_message:
        del test_message["Cc"]

    if test_cc:
        test_message["Cc"] = ", ".join(test_cc)

    subject = str(
        test_message["Subject"]
    )

    test_message.replace_header(
        "Subject",
        f"[TEST] {subject}",
    )

    prepared = PreparedMessage(
        message=test_message,
        envelope_recipients=[
            test_email,
            *test_cc,
        ],
        resolved_attachments=(
            original.resolved_attachments
        ),
    )

    print()
    print("=" * 60)
    print("TEST MODE")
    print("=" * 60)
    print(
        "Personalization from: "
        f"{recipient.display_name}"
    )
    print(
        f"Test delivery to: "
        f"{test_email}"
    )
    print(
        f"Test CC: "
        f"{', '.join(test_cc) if test_cc else 'none'}"
    )
    print("Test BCC: disabled")

    print_prepared_message(
        prepared,
        number=1,
        total=1,
    )

    confirmation = input(
        "Type SEND to send test: "
    ).strip()

    if confirmation != "SEND":
        print(
            "CANCELLED: Nothing was sent."
        )
        return

    password = load_password()

    transport = create_transport(
        config,
        password,
    )

    try:
        transport.connect()

        result = transport.send(
            prepared
        )

    except Exception as error:
        append_log(
            file=TEST_LOG_FILE,
            mailing_id=config.mailing_id,
            recipient_name=(
                recipient.display_name
            ),
            recipient_email=test_email,
            transport=config.transport_type,
            status="ERROR",
            details=(
                f"{type(error).__name__}: "
                f"{error}"
            ),
        )
        raise

    finally:
        transport.close()

    append_log(
        file=TEST_LOG_FILE,
        mailing_id=config.mailing_id,
        recipient_name=(
            recipient.display_name
        ),
        recipient_email=test_email,
        transport=config.transport_type,
        status=result.status,
        details=result_details(result),
    )

    print()
    print("=" * 60)
    print_result(result)
    print(
        f"Test log: {TEST_LOG_FILE}"
    )
    print("=" * 60)


def command_send() -> None:
    config = load_config()
    validate_config(config)

    builder = MessageBuilder(
        config.message_config
    )

    delivered = delivered_addresses(
        config.mailing_id
    )

    pending = []

    print()
    print("=" * 60)
    print("PRODUCTION MAILING")
    print("=" * 60)
    print(
        f"Mailing ID: "
        f"{config.mailing_id}"
    )
    print(
        f"Transport:  "
        f"{config.transport_type}"
    )
    print(
        f"From:       "
        f"{config.from_email}"
    )
    print()

    skipped = 0

    for number, recipient in enumerate(
        config.recipients,
        start=1,
    ):
        previous = delivered.get(
            recipient.email.lower()
        )

        if previous:
            skipped += 1

            print(
                f"{number:2}. "
                f"SKIP ({previous}) "
                f"{recipient.display_name} "
                f"<{recipient.email}>"
            )
        else:
            pending.append(
                recipient
            )

            print(
                f"{number:2}. "
                f"SEND "
                f"{recipient.display_name} "
                f"<{recipient.email}>"
            )

    print()
    print(
        f"Already delivered: {skipped}"
    )
    print(
        f"Will send now:     "
        f"{len(pending)}"
    )

    if not pending:
        print()
        print(
            "NOTHING TO SEND: "
            "all recipients are already "
            "recorded as delivered."
        )
        print(
            f"Log: {SEND_LOG_FILE}"
        )
        return

    expected = (
        f"SEND {len(pending)}"
    )

    print()
    confirmation = input(
        f"Type {expected} to continue: "
    ).strip()

    if confirmation != expected:
        print(
            "CANCELLED: Nothing was sent."
        )
        return

    password = load_password()

    transport = create_transport(
        config,
        password,
    )

    successful = 0
    partial = 0
    failed = 0

    try:
        transport.connect()

        for number, recipient in enumerate(
            pending,
            start=1,
        ):
            print()
            print(
                f"[{number}/{len(pending)}] "
                f"{recipient.display_name} "
                f"<{recipient.email}>"
            )

            try:
                prepared = builder.build(
                    recipient
                )

                result = transport.send(
                    prepared
                )

                append_log(
                    file=SEND_LOG_FILE,
                    mailing_id=(
                        config.mailing_id
                    ),
                    recipient_name=(
                        recipient.display_name
                    ),
                    recipient_email=(
                        recipient.email
                    ),
                    transport=(
                        config.transport_type
                    ),
                    status=result.status,
                    details=(
                        result_details(result)
                    ),
                )

                print_result(result)

                if result.status == "SUCCESS":
                    successful += 1

                elif result.status == "PARTIAL":
                    partial += 1

                else:
                    failed += 1

            except Exception as error:
                failed += 1

                details = (
                    f"{type(error).__name__}: "
                    f"{error}"
                )

                append_log(
                    file=SEND_LOG_FILE,
                    mailing_id=(
                        config.mailing_id
                    ),
                    recipient_name=(
                        recipient.display_name
                    ),
                    recipient_email=(
                        recipient.email
                    ),
                    transport=(
                        config.transport_type
                    ),
                    status="ERROR",
                    details=details,
                )

                print(
                    "ERROR: MESSAGE "
                    "WAS NOT SENT"
                )
                print(
                    f"  {details}"
                )

            if number < len(pending):
                time.sleep(
                    config.delay_seconds
                )

    finally:
        transport.close()

    print()
    print("=" * 60)
    print("MAILING FINISHED")
    print("=" * 60)
    print(
        f"SUCCESS: {successful}"
    )
    print(
        f"PARTIAL: {partial}"
    )
    print(
        f"FAILED:  {failed}"
    )
    print(
        f"SKIPPED: {skipped}"
    )
    print()
    print(
        f"Log: {SEND_LOG_FILE}"
    )
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MEPhI Mail Sender"
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    commands.add_parser(
        "check",
        help="Validate mailing.yaml",
    )

    commands.add_parser(
        "status",
        help="Show delivery status",
    )

    preview_parser = commands.add_parser(
        "preview",
        help="Preview without sending",
    )

    preview_parser.add_argument(
        "--recipient",
        type=int,
        default=None,
        help="Show recipient N only",
    )

    test_parser = commands.add_parser(
        "test",
        help="Send one safe test",
    )

    test_parser.add_argument(
        "email",
        help="Test delivery address",
    )

    test_parser.add_argument(
        "--recipient",
        type=int,
        default=1,
        help="Use personalization of recipient N",
    )

    test_parser.add_argument(
        "--cc",
        action="append",
        default=[],
        help="Add CC recipient to test message",
    )

    commands.add_parser(
        "send",
        help="Run production mailing",
    )

    args = parser.parse_args()

    try:
        if args.command == "check":
            command_check()

        elif args.command == "status":
            command_status()

        elif args.command == "preview":
            command_preview(
                args.recipient
            )

        elif args.command == "test":
            command_test(
                args.email,
                args.recipient,
                args.cc,
            )

        elif args.command == "send":
            command_send()

    except KeyboardInterrupt:
        print()
        print(
            "CANCELLED: Interrupted by user."
        )
        raise SystemExit(130)

    except Exception as error:
        print()
        print("=" * 60)
        print("ERROR")
        print("=" * 60)
        print(
            f"{type(error).__name__}: "
            f"{error}"
        )
        print("=" * 60)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
