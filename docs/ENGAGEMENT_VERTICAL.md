# HIJAZ ONE Customer Engagement Vertical

## Verified Hatchable Version 40 source contract

Read-only inventory of the existing hijaz-construction Hatchable project (Version 40) confirmed:

- Tables: engagement_leads, engagement_messages, engagement_conversations, engagement_chat_messages, engagement_unsubscribe_tokens, engagement_agent_events, membership_plans, and user_memberships.
- API functions: api/engagement.js, api/engagement-admin.js, api/jobs/follow-up.js, and api/unsubscribe.js.
- engagement_leads stores a user-owned interest, preferred language (ur/en), marketing consent, channel-specific email/WhatsApp/SMS consent, follow-up count/status and next-follow-up time.
- The source creates a hashed unsubscribe token when marketing consent is granted. Unsubscribe suppression blocks future follow-up selection.
- The source follow-up job processes at most five due leads per invocation and caps follow-ups at three. It writes in-app messages and may send email only when email consent is true.
- The source customer-reply action stops follow-ups by clearing next_followup_at; user opt-out clears all channel consents and marks the lead opted out.
- The source admin API is admin-only and exposes lead and membership review data.

No source user rows, emails, phone numbers, messages, membership records or unsubscribe tokens were copied. The live Hatchable project remains unchanged.

## Portable implementation in Draft PR #80

- Adds user-owned customer engagement leads with Urdu/English language selection and bounded interest text.
- Adds an append-only consent event ledger with separate marketing, email, SMS and WhatsApp channels and a server-controlled policy version.
- Channel consent cannot be granted without explicit marketing consent. Email consent requires verified email; SMS/WhatsApp consent requires a provider-verified E.164 phone snapshot no older than 30 days.
- No email address or phone number is duplicated in the portable lead or consent tables.
- Unsubscribe tokens are returned once, stored only as SHA-256 digests, expire after one year, and suppress all marketing/channel consent when used. Unknown, expired and previously used tokens receive the same 204 response.
- Users can read and update consent only on their own leads. The platform admin queue is restricted to the verified platform-admin email allowlist and does not return email or phone fields.
- Audit events contain only allowlisted status/reason metadata; consent history is protected by SQLite/PostgreSQL append-only triggers.
- No email, SMS, WhatsApp, AI or payment provider is invoked. No follow-up time is scheduled until the independent portable worker exists.

## Remaining gaps

- Durable independent worker/scheduler and race-safe follow-up claiming.
- In-app message history and bilingual proposal generation.
- Provider-neutral email/SMS/WhatsApp adapters, verified webhooks and delivery reconciliation.
- Consent-policy rendering/version archive and jurisdiction-specific lawful-basis/retention review.
- Admin assignment, escalation, moderation and customer data export/deletion workflows.
- Hatchable export/import, object checksums and data reconciliation remain release gates.
