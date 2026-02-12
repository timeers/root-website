from celery import shared_task
from django.urls import reverse

import logging

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 60},
    retry_backoff=True,
)
def generate_grouping_async(session_id, round_id):
    """
    Async task to generate availability-based groups for a GroupingSession + Round.
    Creates UserNotification when complete.
    """
    from the_warroom.models import GroupingSession, Round
    from the_warroom.services.grouping import GroupingService
    from the_gatehouse.models import UserNotification, MessageChoices

    try:
        session = GroupingSession.objects.select_related('survey', 'created_by').get(id=session_id)
    except GroupingSession.DoesNotExist:
        logger.error(f"GroupingSession {session_id} not found")
        return

    try:
        round_obj = Round.objects.get(id=round_id)
    except Round.DoesNotExist:
        logger.error(f"Round {round_id} not found")
        return

    try:
        GroupingService.generate_availability_groups(session, round_obj)
        round_obj.grouping_status = Round.GroupingStatusChoices.DRAFT
        round_obj.grouping_notes = ''
        round_obj.save(update_fields=['grouping_status', 'grouping_notes'])
        logger.info(f"Successfully generated groups for session {session_id}, round {round_id}")

    except Exception as e:
        logger.exception(f"Error generating groups for session {session_id}, round {round_id}")
        round_obj.grouping_status = Round.GroupingStatusChoices.ERROR
        round_obj.grouping_notes = f"Error during group generation: {str(e)}"
        round_obj.save(update_fields=['grouping_status', 'grouping_notes'])
        raise


