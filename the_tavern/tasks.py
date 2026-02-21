from celery import shared_task
from django.urls import reverse

import logging

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 60},
    retry_backoff=True,
)
def generate_grouping_async(stage_id, round_id):
    """
    Async task to generate availability-based groups for a Stage + Round.
    Creates UserNotification when complete.
    """
    from the_warroom.models import Stage, Round
    from the_warroom.services.grouping import GroupingService

    try:
        stage = Stage.objects.select_related('tournament').get(id=stage_id)
    except Stage.DoesNotExist:
        logger.error(f"Stage {stage_id} not found")
        return

    try:
        round_obj = Round.objects.get(id=round_id)
    except Round.DoesNotExist:
        logger.error(f"Round {round_id} not found")
        return

    try:
        GroupingService.generate_availability_groups(stage, round_obj)
        round_obj.grouping_status = Round.GroupingStatusChoices.DRAFT
        round_obj.grouping_notes = ''
        round_obj.save(update_fields=['grouping_status', 'grouping_notes'])
        logger.info(f"Successfully generated groups for stage {stage_id}, round {round_id}")

    except Exception as e:
        logger.exception(f"Error generating groups for stage {stage_id}, round {round_id}")
        round_obj.grouping_status = Round.GroupingStatusChoices.ERROR
        round_obj.grouping_notes = f"Error during group generation: {str(e)}"
        round_obj.save(update_fields=['grouping_status', 'grouping_notes'])
        raise


