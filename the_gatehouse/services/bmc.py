"""Buy Me a Coffee webhook handling.

Field names follow BMC's webhook OpenAPI spec
(https://cdn.buymeacoffee.com/assets/integrations/bmc-webhooks-openapi.json).
Every `data` field is read with .get() -- a field BMC drops or renames must not
turn a donation into a 500 that BMC then retries (and, after 10 consecutive
failures, auto-disables the webhook over).
"""
import hashlib
import hmac
import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from the_gatehouse.models import Supporter, SupportEvent

logger = logging.getLogger(__name__)

PAYMENT_EVENTS = {'donation.created', 'donation.refunded'}
SUBSCRIPTION_START_EVENTS = {
    'membership.started', 'membership.updated',
    'recurring_donation.started', 'recurring_donation.updated',
}
SUBSCRIPTION_END_EVENTS = {
    'membership.cancelled', 'membership.paused', 'recurring_donation.cancelled',
}

SUPPORT_EMBED_COLOR = 0xD4A017  # Gold

BMC_PAGE_URL = 'https://www.buymeacoffee.com/mrmirz'


def verify_signature(raw_body, secret, signature):
    """BMC signs the raw body: hex HMAC-SHA256 keyed with the webhook secret."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _flag(value):
    # BMC sends booleans as "true"/"false" strings on payments but as real
    # booleans on subscriptions (e.g. note_hidden), so accept both.
    if isinstance(value, str):
        return value.lower() == 'true'
    return bool(value)


def _when(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=dt_timezone.utc) if ts else None
    except (TypeError, ValueError, OverflowError):
        return None


def _amount(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def _dedupe_key(event_type, data, event_id, live_mode):
    if not live_mode:
        return None  # test events always carry event_id 1; let every one land
    object_id = data.get('id')
    if object_id is None:
        return f"{event_type}:event:{event_id}"
    if event_type in PAYMENT_EVENTS:
        return f"{event_type}:{object_id}"
    return f"{event_type}:{object_id}:{event_id}"


ANONYMOUS_NAMES = {'', 'someone'}


def is_anonymous_name(name):
    return (name or '').strip().lower() in ANONYMOUS_NAMES


def _get_or_create_supporter(data, when):
    supporter_id = data.get('supporter_id')
    email = (data.get('supporter_email') or '').strip()
    supporter = None
    if supporter_id is not None:
        supporter = Supporter.objects.filter(bmc_supporter_id=str(supporter_id)).first()
    if supporter is None and email:
        supporter = Supporter.objects.filter(email__iexact=email).first()
    if supporter is None:
        # BMC has no "list me" option: a donor stays anonymous by leaving Name
        # blank, which BMC shows as "Someone". Giving a real name is our opt-in.
        # Decided only on creation so an admin's later toggle is never overwritten.
        supporter = Supporter(first_supported=when, show_publicly=not is_anonymous_name(data.get('supporter_name')))

    if supporter_id is not None and not supporter.bmc_supporter_id:
        supporter.bmc_supporter_id = str(supporter_id)
    if email:
        supporter.email = email
    if data.get('supporter_name'):
        supporter.name = data['supporter_name'][:200]
    if supporter.first_supported is None:
        supporter.first_supported = when
    return supporter


def process_event(payload):
    """Record one webhook event. Returns the SupportEvent, or None when it is a
    duplicate delivery that was already recorded."""
    event_type = str(payload.get('type') or '')
    data = payload.get('data') or {}
    live_mode = _flag(payload.get('live_mode', True))
    event_id = str(payload.get('event_id') or '')
    when = _when(data.get('created_at') or payload.get('created')) or timezone.now()

    try:
        with transaction.atomic():
            event = SupportEvent.objects.create(
                dedupe_key=_dedupe_key(event_type, data, event_id, live_mode),
                event_id=event_id,
                event_type=event_type,
                bmc_object_id=str(data.get('id') or ''),
                amount=_amount(data.get('amount')),
                currency=str(data.get('currency') or '')[:8],
                live_mode=live_mode,
                refunded=event_type == 'donation.refunded',
                created=when,
                payload=payload,
            )
            if live_mode:
                _apply(event, data, when)
            transaction.on_commit(lambda: _notify(event, data))
    except IntegrityError:
        logger.info("BMC webhook: duplicate delivery %s ignored", event_type)
        return None
    return event


def _apply(event, data, when):
    event_type = event.event_type

    if event_type == 'donation.refunded':
        SupportEvent.objects.filter(
            event_type='donation.created', bmc_object_id=event.bmc_object_id,
        ).update(refunded=True)
        event.supporter = Supporter.objects.filter(events__bmc_object_id=event.bmc_object_id).first()
        event.save(update_fields=['supporter'])
        return

    if event_type == 'donation.created':
        supporter = _get_or_create_supporter(data, when)
        supporter.last_supported = max(filter(None, [supporter.last_supported, when]))

    elif event_type in SUBSCRIPTION_START_EVENTS:
        supporter = _get_or_create_supporter(data, when)
        active = data.get('status', 'active') == 'active' and not _flag(data.get('paused'))
        supporter.is_member = active
        supporter.membership_started = _when(data.get('started_at')) or supporter.membership_started or when
        supporter.membership_ends = _when(data.get('current_period_end')) if active else _when(data.get('canceled_at'))
        if event_type.endswith('.started'):
            supporter.last_supported = max(filter(None, [supporter.last_supported, when]))

    elif event_type in SUBSCRIPTION_END_EVENTS:
        supporter = _get_or_create_supporter(data, when)
        supporter.is_member = False
        if _flag(data.get('cancel_at_period_end')):
            supporter.membership_ends = _when(data.get('current_period_end')) or when
        else:
            supporter.membership_ends = _when(data.get('canceled_at') or data.get('paused_at')) or when

    else:
        logger.info("BMC webhook: unhandled event type %r recorded", event_type)
        return

    supporter.save()
    event.supporter = supporter
    event.save(update_fields=['supporter'])


def _describe(event, data):
    name = data.get('supporter_name') or 'Someone'
    amount = f"{event.amount} {event.currency}".strip() if event.amount is not None else ''
    level = data.get('membership_level_name')
    t = event.event_type
    if t == 'donation.created':
        count = data.get('coffee_count')
        coffees = f"{count} coffee{'s' if count != 1 else ''}" if count else 'a coffee'
        return f"**{name}** bought {coffees}" + (f" ({amount})" if amount else '')
    if t == 'donation.refunded':
        return f"Donation from **{name}** was refunded" + (f" ({amount})" if amount else '')
    if t in ('membership.started', 'recurring_donation.started'):
        kind = f"a **{level}** member" if level else ('a member' if t.startswith('membership') else 'a monthly supporter')
        return f"**{name}** became {kind}" + (f" ({amount}/{data.get('duration_type', 'month')})" if amount else '')
    if t in ('membership.updated', 'recurring_donation.updated'):
        return f"**{name}** updated their support"
    if t == 'membership.paused':
        return f"**{name}** paused their membership"
    if t in SUBSCRIPTION_END_EVENTS:
        return f"**{name}** ended their support"
    # Extras, commissions and wishlist payments carry BMC's own readable summary.
    if data.get('message'):
        return str(data['message'])[:500]
    return f"**{name}**: `{t}`"


def _notify(event, data):
    from the_gatehouse.tasks import send_rich_discord_message_task
    from kombu.exceptions import OperationalError

    message = _describe(event, data)
    note = data.get('support_note')
    fields = []
    if note:
        label = 'Note (hidden on BMC)' if _flag(data.get('note_hidden')) else 'Note'
        fields.append({'name': label, 'value': str(note)[:1000]})
    title = 'Support Received' if event.live_mode else '[TEST] Support Received'
    try:
        send_rich_discord_message_task.delay(
            message, category='support', title=title, fields=fields or None,
            color=SUPPORT_EMBED_COLOR,
        )
    except OperationalError:
        # Broker down: the event is already saved, so don't fail the webhook over a ping.
        logger.warning("BMC webhook: Celery broker unavailable; Discord ping skipped")
