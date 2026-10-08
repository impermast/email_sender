# MEPhI Mail Sender

Personalized email sender for MEPhI mail accounts.

Supported transports:
- EWS
- SMTP

EWS is recommended because sent messages are saved in Outlook Sent Items.

## Installation

Install Python 3 and dependencies:

    pip install -r requirements.txt

Create your configuration:

    cp mailing.example.yaml mailing.yaml

Edit mailing.yaml.

Create pass.txt in the same directory.
It must contain only your MEPhI mail password.

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

    python main.py check

Preview messages without sending:

    python main.py preview

Send a safe test:

    python main.py test your_email@example.com

Test with CC:

    python main.py test your_email@example.com --cc another@example.com

Show current mailing status:

    python main.py status

Start production mailing:

    python main.py send

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

    python main.py send

again will skip recipients already marked as successfully delivered.

Use a new mailing_id for every new mailing.

## Security

Never share:

    pass.txt

Do not put passwords directly into mailing.yaml.
