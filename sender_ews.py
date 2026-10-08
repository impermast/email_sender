from __future__ import annotations

import base64
import mimetypes
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import getaddresses
from pathlib import Path
from typing import Iterable, Sequence

import requests
from requests_ntlm import HttpNtlmAuth


SOAP_NS = (
    "http://schemas.xmlsoap.org/soap/envelope/"
)
MESSAGES_NS = (
    "http://schemas.microsoft.com/"
    "exchange/services/2006/messages"
)
TYPES_NS = (
    "http://schemas.microsoft.com/"
    "exchange/services/2006/types"
)

NS = {
    "soap": SOAP_NS,
    "m": MESSAGES_NS,
    "t": TYPES_NS,
}

ET.register_namespace("soap", SOAP_NS)
ET.register_namespace("m", MESSAGES_NS)
ET.register_namespace("t", TYPES_NS)


def q(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


class EWSError(RuntimeError):
    pass


class EWSAuthenticationError(EWSError):
    pass


class EWSResponseError(EWSError):
    pass


@dataclass(frozen=True)
class EWSConfig:
    endpoint: str = (
        "https://mail-ca.mephi.ru/"
        "EWS/Exchange.asmx"
    )
    domain: str = "MEPHI"
    exchange_version: str = "Exchange2010_SP2"
    timeout: int = 30
    verify_tls: bool = True


@dataclass(frozen=True)
class EWSResult:
    saved_to_sent: bool
    item_id: str | None = None


class MephiEWSSender:
    def __init__(
        self,
        *,
        username: str,
        password: str,
        from_email: str,
        config: EWSConfig | None = None,
        debug: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.from_email = from_email
        self.config = config or EWSConfig()
        self.debug = debug
        self.session: requests.Session | None = None

    @property
    def ntlm_username(self) -> str:
        if "\\" in self.username:
            return self.username

        return (
            f"{self.config.domain}\\"
            f"{self.username}"
        )

    def connect(self) -> None:
        session = requests.Session()

        session.auth = HttpNtlmAuth(
            self.ntlm_username,
            self.password,
        )

        session.headers.update(
            {
                "Content-Type": (
                    "text/xml; charset=utf-8"
                ),
                "Accept": "text/xml",
                "User-Agent": "MEPhI-Mail-Sender/1.0",
            }
        )

        self.session = session

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None

    def send(
        self,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
        attachments: Sequence[
            str | Path
        ] = (),
    ) -> EWSResult:
        if self.session is None:
            self.connect()

        attachment_paths = [
            Path(path)
            for path in attachments
        ]

        if attachment_paths:
            return self._send_with_attachments(
                message,
                envelope_recipients,
                attachment_paths,
            )

        return self._send_direct(
            message,
            envelope_recipients,
        )

    def _send_direct(
        self,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
    ) -> EWSResult:
        envelope, body = self._new_envelope()

        create_item = ET.SubElement(
            body,
            q(MESSAGES_NS, "CreateItem"),
            {
                "MessageDisposition":
                    "SendAndSaveCopy"
            },
        )

        saved_folder = ET.SubElement(
            create_item,
            q(
                MESSAGES_NS,
                "SavedItemFolderId",
            ),
        )

        ET.SubElement(
            saved_folder,
            q(
                TYPES_NS,
                "DistinguishedFolderId",
            ),
            {"Id": "sentitems"},
        )

        items = ET.SubElement(
            create_item,
            q(MESSAGES_NS, "Items"),
        )

        self._add_message(
            items,
            message,
            envelope_recipients,
        )

        response = self._post(envelope)

        self._ensure_success(
            response,
            "SendAndSaveCopy",
        )

        return EWSResult(
            saved_to_sent=True,
        )

    def _send_with_attachments(
        self,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
        attachments: Sequence[Path],
    ) -> EWSResult:
        item_id, change_key = (
            self._create_draft(
                message,
                envelope_recipients,
            )
        )

        item_id, change_key = (
            self._create_attachments(
                item_id,
                change_key,
                attachments,
            )
        )

        self._send_draft(
            item_id,
            change_key,
        )

        return EWSResult(
            saved_to_sent=True,
            item_id=item_id,
        )

    def _create_draft(
        self,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
    ) -> tuple[str, str]:
        envelope, body = self._new_envelope()

        create_item = ET.SubElement(
            body,
            q(MESSAGES_NS, "CreateItem"),
            {
                "MessageDisposition":
                    "SaveOnly"
            },
        )

        saved_folder = ET.SubElement(
            create_item,
            q(
                MESSAGES_NS,
                "SavedItemFolderId",
            ),
        )

        ET.SubElement(
            saved_folder,
            q(
                TYPES_NS,
                "DistinguishedFolderId",
            ),
            {"Id": "drafts"},
        )

        items = ET.SubElement(
            create_item,
            q(MESSAGES_NS, "Items"),
        )

        self._add_message(
            items,
            message,
            envelope_recipients,
        )

        response = self._post(envelope)

        self._ensure_success(
            response,
            "Create draft",
        )

        item = response.find(
            ".//t:ItemId",
            NS,
        )

        if item is None:
            raise EWSResponseError(
                "EWS did not return draft ItemId."
            )

        item_id = item.attrib.get("Id")
        change_key = item.attrib.get(
            "ChangeKey"
        )

        if not item_id or not change_key:
            raise EWSResponseError(
                "Draft ItemId or ChangeKey "
                "is missing."
            )

        return item_id, change_key

    def _create_attachments(
        self,
        item_id: str,
        change_key: str,
        attachments: Sequence[Path],
    ) -> tuple[str, str]:
        envelope, body = self._new_envelope()

        create_attachment = ET.SubElement(
            body,
            q(
                MESSAGES_NS,
                "CreateAttachment",
            ),
        )

        ET.SubElement(
            create_attachment,
            q(
                MESSAGES_NS,
                "ParentItemId",
            ),
            {
                "Id": item_id,
                "ChangeKey": change_key,
            },
        )

        attachment_container = ET.SubElement(
            create_attachment,
            q(MESSAGES_NS, "Attachments"),
        )

        for path in attachments:
            if not path.is_file():
                raise FileNotFoundError(
                    f"Attachment not found: "
                    f"{path}"
                )

            file_attachment = ET.SubElement(
                attachment_container,
                q(
                    TYPES_NS,
                    "FileAttachment",
                ),
            )

            ET.SubElement(
                file_attachment,
                q(TYPES_NS, "Name"),
            ).text = path.name

            content_type, _ = (
                mimetypes.guess_type(
                    path.name
                )
            )

            if content_type:
                ET.SubElement(
                    file_attachment,
                    q(
                        TYPES_NS,
                        "ContentType",
                    ),
                ).text = content_type

            content = base64.b64encode(
                path.read_bytes()
            ).decode("ascii")

            ET.SubElement(
                file_attachment,
                q(TYPES_NS, "Content"),
            ).text = content

        response = self._post(envelope)

        self._ensure_success(
            response,
            "CreateAttachment",
        )

        attachment_id = response.find(
            ".//t:AttachmentId",
            NS,
        )

        if attachment_id is None:
            raise EWSResponseError(
                "EWS did not return "
                "AttachmentId."
            )

        new_item_id = (
            attachment_id.attrib.get(
                "RootItemId"
            )
            or item_id
        )

        new_change_key = (
            attachment_id.attrib.get(
                "RootItemChangeKey"
            )
            or change_key
        )

        return (
            new_item_id,
            new_change_key,
        )

    def _send_draft(
        self,
        item_id: str,
        change_key: str,
    ) -> None:
        envelope, body = self._new_envelope()

        send_item = ET.SubElement(
            body,
            q(MESSAGES_NS, "SendItem"),
            {
                "SaveItemToFolder": "true"
            },
        )

        item_ids = ET.SubElement(
            send_item,
            q(MESSAGES_NS, "ItemIds"),
        )

        ET.SubElement(
            item_ids,
            q(TYPES_NS, "ItemId"),
            {
                "Id": item_id,
                "ChangeKey": change_key,
            },
        )

        saved_folder = ET.SubElement(
            send_item,
            q(
                MESSAGES_NS,
                "SavedItemFolderId",
            ),
        )

        ET.SubElement(
            saved_folder,
            q(
                TYPES_NS,
                "DistinguishedFolderId",
            ),
            {"Id": "sentitems"},
        )

        response = self._post(envelope)

        self._ensure_success(
            response,
            "SendItem",
        )

    def _new_envelope(
        self,
    ) -> tuple[
        ET.Element,
        ET.Element,
    ]:
        envelope = ET.Element(
            q(SOAP_NS, "Envelope")
        )

        header = ET.SubElement(
            envelope,
            q(SOAP_NS, "Header"),
        )

        ET.SubElement(
            header,
            q(
                TYPES_NS,
                "RequestServerVersion",
            ),
            {
                "Version":
                    self.config.exchange_version
            },
        )

        body = ET.SubElement(
            envelope,
            q(SOAP_NS, "Body"),
        )

        return envelope, body

    def _add_message(
        self,
        parent: ET.Element,
        message: EmailMessage,
        envelope_recipients: Iterable[str],
    ) -> None:
        to_addresses = self._header_addresses(
            message,
            "To",
        )

        cc_addresses = self._header_addresses(
            message,
            "Cc",
        )

        visible = {
            address.lower()
            for address in (
                to_addresses
                + cc_addresses
            )
        }

        bcc_addresses = []

        for address in envelope_recipients:
            address = str(address).strip()

            if (
                address
                and address.lower()
                not in visible
            ):
                bcc_addresses.append(
                    address
                )

        bcc_addresses = self._unique(
            bcc_addresses
        )

        if not to_addresses:
            raise EWSError(
                "Message has no To recipient."
            )

        item = ET.SubElement(
            parent,
            q(TYPES_NS, "Message"),
        )

        ET.SubElement(
            item,
            q(TYPES_NS, "ItemClass"),
        ).text = "IPM.Note"

        ET.SubElement(
            item,
            q(TYPES_NS, "Subject"),
        ).text = str(
            message.get(
                "Subject",
                "",
            )
        )

        text, body_type = (
            self._extract_body(
                message
            )
        )

        body_element = ET.SubElement(
            item,
            q(TYPES_NS, "Body"),
            {
                "BodyType": body_type
            },
        )

        body_element.text = text

        self._add_recipients(
            item,
            "ToRecipients",
            to_addresses,
        )

        self._add_recipients(
            item,
            "CcRecipients",
            cc_addresses,
        )

        self._add_recipients(
            item,
            "BccRecipients",
            bcc_addresses,
        )

    def _add_recipients(
        self,
        parent: ET.Element,
        element_name: str,
        addresses: Sequence[str],
    ) -> None:
        if not addresses:
            return

        container = ET.SubElement(
            parent,
            q(
                TYPES_NS,
                element_name,
            ),
        )

        for address in addresses:
            mailbox = ET.SubElement(
                container,
                q(TYPES_NS, "Mailbox"),
            )

            ET.SubElement(
                mailbox,
                q(
                    TYPES_NS,
                    "EmailAddress",
                ),
            ).text = address

    def _header_addresses(
        self,
        message: EmailMessage,
        name: str,
    ) -> list[str]:
        values = message.get_all(
            name,
            [],
        )

        addresses = [
            address.strip()
            for _, address
            in getaddresses(values)
            if address.strip()
        ]

        return self._unique(addresses)

    def _extract_body(
        self,
        message: EmailMessage,
    ) -> tuple[str, str]:
        part = message.get_body(
            preferencelist=(
                "html",
                "plain",
            )
        )

        if part is None:
            return "", "Text"

        content = part.get_content()

        if (
            part.get_content_subtype()
            == "html"
        ):
            return str(content), "HTML"

        return str(content), "Text"

    def _post(
        self,
        envelope: ET.Element,
    ) -> ET.Element:
        if self.session is None:
            raise EWSError(
                "EWS session is not connected."
            )

        payload = ET.tostring(
            envelope,
            encoding="utf-8",
            xml_declaration=True,
        )

        if self.debug:
            print(
                f"EWS POST "
                f"{self.config.endpoint}"
            )

        try:
            response = self.session.post(
                self.config.endpoint,
                data=payload,
                timeout=self.config.timeout,
                verify=self.config.verify_tls,
            )
        except requests.RequestException as error:
            raise EWSError(
                f"EWS request failed: {error}"
            ) from error

        try:
            root = ET.fromstring(
                response.content
            )
        except ET.ParseError as error:
            text = response.text[:500]

            raise EWSResponseError(
                f"EWS returned HTTP "
                f"{response.status_code} "
                f"with invalid XML: "
                f"{text}"
            ) from error

        if response.status_code == 401:
            raise EWSAuthenticationError(
                "EWS authentication failed "
                "(HTTP 401)."
            )

        if response.status_code >= 400:
            details = self._response_error(
                root
            )

            raise EWSResponseError(
                f"EWS HTTP "
                f"{response.status_code}: "
                f"{details}"
            )

        return root

    def _ensure_success(
        self,
        root: ET.Element,
        operation: str,
    ) -> None:
        responses = root.findall(
            ".//*[@ResponseClass]"
        )

        if not responses:
            fault = root.find(
                ".//soap:Fault",
                NS,
            )

            if fault is not None:
                raise EWSResponseError(
                    f"{operation}: "
                    f"{self._fault_text(fault)}"
                )

            raise EWSResponseError(
                f"{operation}: EWS returned "
                f"no response status."
            )

        errors = []

        for response in responses:
            response_class = (
                response.attrib.get(
                    "ResponseClass"
                )
            )

            if response_class == "Success":
                continue

            code = response.findtext(
                "m:ResponseCode",
                default="UnknownError",
                namespaces=NS,
            )

            message = response.findtext(
                "m:MessageText",
                default="",
                namespaces=NS,
            )

            errors.append(
                f"{code}: {message}".strip()
            )

        if errors:
            raise EWSResponseError(
                f"{operation}: "
                + " | ".join(errors)
            )

    def _response_error(
        self,
        root: ET.Element,
    ) -> str:
        fault = root.find(
            ".//soap:Fault",
            NS,
        )

        if fault is not None:
            return self._fault_text(
                fault
            )

        response = root.find(
            ".//*[@ResponseClass='Error']"
        )

        if response is not None:
            code = response.findtext(
                "m:ResponseCode",
                default="UnknownError",
                namespaces=NS,
            )

            text = response.findtext(
                "m:MessageText",
                default="",
                namespaces=NS,
            )

            return (
                f"{code}: {text}"
            ).strip()

        return "Unknown EWS error."

    @staticmethod
    def _fault_text(
        fault: ET.Element,
    ) -> str:
        parts = []

        for child in fault:
            name = child.tag.split(
                "}",
                1,
            )[-1]

            if name in {
                "faultcode",
                "faultstring",
            }:
                if child.text:
                    parts.append(
                        child.text.strip()
                    )

        return (
            ": ".join(parts)
            or "SOAP fault"
        )

    @staticmethod
    def _unique(
        addresses: Iterable[str],
    ) -> list[str]:
        result = []
        seen = set()

        for address in addresses:
            address = str(
                address
            ).strip()

            key = address.lower()

            if not address or key in seen:
                continue

            seen.add(key)
            result.append(address)

        return result

    def __enter__(
        self,
    ) -> "MephiEWSSender":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.close()
