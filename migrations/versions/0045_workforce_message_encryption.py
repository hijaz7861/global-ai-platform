"""Encrypt existing workforce message bodies before the API serves them."""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

revision: str = "0045_workforce_message_encryption"
down_revision: Union[str, None] = "0044_workforce_contact_relay"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREFIX = "fernet:v1:"


def upgrade() -> None:
    connection = op.get_bind()
    messages = sa.table(
        "workforce_service_messages",
        sa.column("id", sa.String(36)),
        sa.column("body", sa.Text()),
    )
    rows = connection.execute(sa.select(messages.c.id, messages.c.body)).all()
    if rows:
        current_key = os.environ.get("WORKFORCE_MESSAGE_ENCRYPTION_KEY", "").strip()
        if not current_key:
            raise RuntimeError(
                "WORKFORCE_MESSAGE_ENCRYPTION_KEY is required to encrypt existing workforce messages"
            )
        previous = os.environ.get("WORKFORCE_MESSAGE_PREVIOUS_ENCRYPTION_KEYS", "")
        try:
            current = Fernet(current_key.encode("ascii"))
            previous_keys = [
                Fernet(item.strip().encode("ascii"))
                for item in previous.split(",")
                if item.strip()
            ]
            keyring = MultiFernet([current, *previous_keys])
        except (ValueError, UnicodeEncodeError):
            raise RuntimeError("Workforce message encryption key configuration is invalid") from None

        for row in rows:
            value = row.body
            try:
                if value.startswith(_PREFIX):
                    plaintext = keyring.decrypt(value[len(_PREFIX):].encode("ascii"))
                else:
                    plaintext = value.encode("utf-8")
            except (InvalidToken, UnicodeEncodeError):
                raise RuntimeError(
                    "An existing workforce message cannot be authenticated; migration stopped"
                ) from None
            encrypted = _PREFIX + current.encrypt(plaintext).decode("ascii")
            connection.execute(
                messages.update().where(messages.c.id == row.id).values(body=encrypted)
            )

    with op.batch_alter_table("workforce_service_messages") as batch:
        batch.drop_constraint("ck_workforce_service_message_body_length", type_="check")
        batch.create_check_constraint(
            "ck_workforce_service_message_body_length",
            "length(body) BETWEEN 1 AND 4096",
        )


def downgrade() -> None:
    connection = op.get_bind()
    encrypted_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM workforce_service_messages "
            "WHERE body LIKE 'fernet:v1:%'"
        )
    ).scalar_one()
    if encrypted_count:
        raise RuntimeError(
            "Cannot downgrade while encrypted workforce messages exist; preserve ciphertext and keys"
        )
    with op.batch_alter_table("workforce_service_messages") as batch:
        batch.drop_constraint("ck_workforce_service_message_body_length", type_="check")
        batch.create_check_constraint(
            "ck_workforce_service_message_body_length",
            "length(body) BETWEEN 1 AND 2000",
        )
