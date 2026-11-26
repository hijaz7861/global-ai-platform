"""Add user-owned engagement leads and an append-only consent ledger."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0049_customer_engagement"
down_revision: Union[str, None] = "0048_construction_labour_mat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_engagement_leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False, server_default="account"),
        sa.Column("interest_category", sa.String(80), nullable=False, server_default="general"),
        sa.Column("interest_text", sa.String(3000), nullable=False),
        sa.Column("preferred_language", sa.String(2), nullable=False, server_default="ur"),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("followup_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("length(trim(interest_text)) > 0", name="ck_customer_engagement_interest_text"),
        sa.CheckConstraint("preferred_language IN ('ur','en')", name="ck_customer_engagement_language"),
        sa.CheckConstraint(
            "status IN ('new','proposal_sent','negotiating','won','lost','opted_out','closed')",
            name="ck_customer_engagement_status",
        ),
        sa.CheckConstraint("followup_count BETWEEN 0 AND 3", name="ck_customer_engagement_followup_count"),
    )
    op.create_index(
        "ix_customer_engagement_user_created",
        "customer_engagement_leads",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_customer_engagement_followup_due",
        "customer_engagement_leads",
        ["next_followup_at", "status"],
    )

    op.create_table(
        "customer_engagement_consent_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("customer_engagement_leads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "channel IN ('marketing','email','sms','whatsapp')",
            name="ck_customer_engagement_consent_channel",
        ),
    )
    op.create_index(
        "ix_customer_engagement_consent_lead_channel_created",
        "customer_engagement_consent_events",
        ["lead_id", "channel", "created_at"],
    )
    op.create_index(
        "ix_customer_engagement_consent_user_created",
        "customer_engagement_consent_events",
        ["user_id", "created_at"],
    )

    op.create_table(
        "customer_engagement_unsubscribe_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("lead_id", sa.String(36), sa.ForeignKey("customer_engagement_leads.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index(
        "ix_customer_engagement_unsubscribe_lead",
        "customer_engagement_unsubscribe_tokens",
        ["lead_id", "created_at"],
    )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_customer_engagement_consent_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'customer engagement consent events are append-only';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_customer_engagement_consent_no_update
            BEFORE UPDATE ON customer_engagement_consent_events
            FOR EACH ROW EXECUTE FUNCTION reject_customer_engagement_consent_mutation()
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_customer_engagement_consent_no_delete
            BEFORE DELETE ON customer_engagement_consent_events
            FOR EACH ROW EXECUTE FUNCTION reject_customer_engagement_consent_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_customer_engagement_consent_no_update
            BEFORE UPDATE ON customer_engagement_consent_events
            BEGIN
                SELECT RAISE(ABORT, 'customer engagement consent events are append-only');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_customer_engagement_consent_no_delete
            BEFORE DELETE ON customer_engagement_consent_events
            BEGIN
                SELECT RAISE(ABORT, 'customer engagement consent events are append-only');
            END
            """
        )


def downgrade() -> None:
    connection = op.get_bind()
    for table in (
        "customer_engagement_unsubscribe_tokens",
        "customer_engagement_consent_events",
        "customer_engagement_leads",
    ):
        count = connection.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError("Cannot downgrade while customer engagement consent records exist")

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_customer_engagement_consent_no_update "
            "ON customer_engagement_consent_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_customer_engagement_consent_no_delete "
            "ON customer_engagement_consent_events"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_customer_engagement_consent_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_customer_engagement_consent_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_customer_engagement_consent_no_delete")

    op.drop_index("ix_customer_engagement_unsubscribe_lead", table_name="customer_engagement_unsubscribe_tokens")
    op.drop_table("customer_engagement_unsubscribe_tokens")
    op.drop_index("ix_customer_engagement_consent_user_created", table_name="customer_engagement_consent_events")
    op.drop_index("ix_customer_engagement_consent_lead_channel_created", table_name="customer_engagement_consent_events")
    op.drop_table("customer_engagement_consent_events")
    op.drop_index("ix_customer_engagement_followup_due", table_name="customer_engagement_leads")
    op.drop_index("ix_customer_engagement_user_created", table_name="customer_engagement_leads")
    op.drop_table("customer_engagement_leads")
