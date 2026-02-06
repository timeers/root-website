from celery import shared_task
from django.urls import reverse

import logging

logger = logging.getLogger(__name__)


@shared_task(
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 60},
    retry_backoff=True,
)
def generate_grouping_async(session_id):
    """
    Async task to generate groups for a GroupingSession.
    Uses session.include_waitlist to determine whether to include waitlisted responses.
    Creates UserNotification when complete.
    """
    from the_warroom.models import GroupingSession
    from the_warroom.services.grouping import GroupingService
    from the_gatehouse.models import UserNotification, MessageChoices

    try:
        session = GroupingSession.objects.select_related('survey', 'created_by').get(id=session_id)
    except GroupingSession.DoesNotExist:
        logger.error(f"GroupingSession {session_id} not found")
        return

    try:
        print('grouping')
        GroupingService.generate_availability_groups(session)
        session.status = GroupingSession.StatusChoices.DRAFT
        session.save(update_fields=['status'])

        # Create notification for creator
        # if session.created_by:
        #     survey_title = session.survey.title if session.survey else session.name
        #     UserNotification.objects.create(
        #         profile=session.created_by,
        #         message=f"Groups are ready for '{survey_title}'",
        #         message_type=MessageChoices.SUCCESS,
        #         related_url=reverse('survey-grouping-organize', args=[session.survey.slug, session.id])
        #     )

        logger.info(f"Successfully generated groups for session {session_id}")

    except Exception as e:
        logger.exception(f"Error generating groups for session {session_id}")
        session.status = GroupingSession.StatusChoices.ERROR
        session.notes = f"Error during group generation: {str(e)}"
        session.save(update_fields=['status', 'notes'])

        # Notify creator of error
        if session.created_by:
            survey_title = session.survey.title if session.survey else session.name
            UserNotification.objects.create(
                profile=session.created_by,
                message=f"Error generating groups for '{survey_title}'. Please try again.",
                message_type=MessageChoices.ERROR,
                related_url=reverse('survey-grouping-setup', args=[session.survey.slug])
            )
        raise


