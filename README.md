# MEPhI Mail Sender

Personalized email sender for MEPhI mail accounts.

Supported transports:
- EWS
- SMTP

EWS is recommended because sent messages are saved in Outlook Sent Items.

## Installation

Use Python 3.10 or newer. Create a virtual environment and install dependencies:

    python3 -m venv .venv
    . .venv/bin/activate
    python3 -m pip install -r requirements.txt

Run the interactive first-time setup:

    python3 get_start.py

The setup asks for the MEPhI login and password, derives the mailbox address,
updates only the `account` section of `mailing.yaml`, writes the password to
`pass.txt` with owner-only permissions, and sends a test message to the same
mailbox. Supported login formats are `ivanov`, `MEPHI\\ivanov`, and
`ivanov@mephi.ru`.

If you prefer to prepare the configuration manually instead of using
`get_start.py`:

    cp mailing.example.yaml mailing.yaml

Then edit `mailing.yaml` and create `pass.txt` with the MEPhI password.

If `mailing.yaml` does not exist, `get_start.py` creates it from
`mailing.example.yaml`. Edit its recipients, subject, message, and attachments
before using the production command.

NEVER send or commit pass.txt.

## Transport

EWS:

    transport:
      type: ews

SMTP:

    transport:
      type: smtp

## Commands

Check configuration:

    python3 main.py check

Preview messages without sending:

    python3 main.py preview

Send a safe test:

    python3 main.py test your_email@example.com

Test with CC:

    python3 main.py test your_email@example.com --cc another@example.com

Show current mailing status:

    python3 main.py status

Start production mailing:

    python3 main.py send

The program asks for explicit confirmation before production sending.

## Attachments

Put attachment files into:

    attachments/

Then reference them from mailing.yaml.

Example:

    global_attachments:
      - schedule.pdf

Recipient-specific attachments are also supported.

## Logs

Logs are created automatically:

    logs/send_log.csv
    logs/test_log.csv

Successful production deliveries are remembered.

If the program is interrupted, running:

    python3 main.py send

again will skip recipients already marked as successfully delivered.

Use a new mailing_id for every new mailing.

## Security

Never share:

    pass.txt

Do not put passwords directly into `mailing.yaml`. The files `mailing.yaml`,
`pass.txt`, `logs/`, and `.venv/` are excluded from Git by default. Review the
recipient list and run `python3 main.py preview` before every production send.
