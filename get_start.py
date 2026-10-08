from __future__ import annotations

import getpass
import os
import sys
import tempfile
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import yaml


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "mailing.yaml"
EXAMPLE_CONFIG_FILE = BASE_DIR / "mailing.example.yaml"
PASSWORD_FILE = BASE_DIR / "pass.txt"

TEST_SUBJECT = "[TEST] MEPhI Mail Sender: setup complete"
TEST_BODY = """Все настроено!

Это тестовое сообщение от MEPhI Mail Sender.
Если вы видите это письмо, логин, пароль и транспорт работают.
"""


def normalize_account(login: str) -> tuple[str, str]:
    login = login.strip()
    if not login:
        raise ValueError("Логин не может быть пустым.")

    if "\\" in login:
        account_username = login
        mailbox_name = login.rsplit("\\", 1)[1]
        email = f"{mailbox_name}@mephi.ru"
    elif "@" in login:
        mailbox_name, domain = login.split("@", 1)
        account_username = mailbox_name
        email = f"{mailbox_name}@{domain}"
    else:
        account_username = login
        mailbox_name = login
        email = f"{login}@mephi.ru"

    if not mailbox_name:
        raise ValueError("Не удалось определить имя почтового ящика.")

    return account_username, email


def load_config() -> tuple[dict, bool]:
    created_from_example = not CONFIG_FILE.exists()
    source = (
        EXAMPLE_CONFIG_FILE
        if created_from_example
        else CONFIG_FILE
    )

    if not source.is_file():
        raise FileNotFoundError(f"Файл конфигурации не найден: {source}")

    with source.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"{source.name} должен содержать YAML-объект.")

    return data, created_from_example


def write_config(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CONFIG_FILE.parent,
            prefix=f".{CONFIG_FILE.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            yaml.safe_dump(
                data,
                handle,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            temporary_path = handle.name

        os.replace(temporary_path, CONFIG_FILE)
        temporary_path = None
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def write_password(password: str) -> None:
    PASSWORD_FILE.write_text(
        password + "\n",
        encoding="utf-8",
    )
    os.chmod(PASSWORD_FILE, 0o600)


def get_transport_type(data: dict) -> str:
    transport = data.get("transport", {"type": "smtp"})

    if isinstance(transport, dict):
        transport = transport.get("type", "smtp")

    transport_type = str(transport).strip().lower()
    if transport_type not in {"smtp", "ews"}:
        raise ValueError(
            f"Неподдерживаемый transport: {transport_type}"
        )

    return transport_type


def send_setup_test(
    *,
    username: str,
    email: str,
    password: str,
    transport_type: str,
) -> object:
    from main import create_transport
    from message_builder import PreparedMessage

    message = EmailMessage()
    message["From"] = email
    message["To"] = email
    message["Subject"] = TEST_SUBJECT
    message.set_content(TEST_BODY)

    prepared = PreparedMessage(
        message=message,
        envelope_recipients=[email],
        resolved_attachments=[],
    )

    config = SimpleNamespace(
        transport_type=transport_type,
        username=username,
        from_email=email,
    )
    transport = create_transport(config, password)

    try:
        transport.connect()
        return transport.send(prepared)
    finally:
        transport.close()


def main() -> int:
    print("MEPhI Mail Sender setup")
    print("Введите логин: ivanov, MEPHI\\ivanov или ivanov@mephi.ru")
    login = input("Логин: ")
    password = getpass.getpass("Пароль: ")
    password_confirmation = getpass.getpass("Повторите пароль: ")

    if password != password_confirmation:
        raise ValueError("Пароли не совпадают.")
    if not password:
        raise ValueError("Пароль не может быть пустым.")

    username, email = normalize_account(login)
    data, created_from_example = load_config()

    account = data.get("account") or {}
    if not isinstance(account, dict):
        raise ValueError("Поле account в mailing.yaml должно быть объектом.")

    account["username"] = username
    account["email"] = email
    data["account"] = account

    write_config(data)
    write_password(password)

    transport_type = get_transport_type(data)
    if created_from_example:
        print("Создан mailing.yaml на основе mailing.example.yaml.")
    else:
        print("Обновлены только account.username и account.email.")
    print("Пароль сохранён в pass.txt с правами доступа 600.")
    print(f"Отправляю тестовое письмо на {email} через {transport_type}...")

    result = send_setup_test(
        username=username,
        email=email,
        password=password,
        transport_type=transport_type,
    )

    status = getattr(result, "status", "SUCCESS")
    success = getattr(result, "success", True)
    if not success:
        raise RuntimeError(f"Тестовое письмо не отправлено: {status}")

    print(f"Готово: тестовое письмо отправлено ({status}).")
    print("Теперь заполните recipients и текст рассылки в mailing.yaml.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nОперация отменена.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        raise SystemExit(1)
