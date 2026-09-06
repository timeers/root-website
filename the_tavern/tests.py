"""Tests for the_tavern: surveys, and the availability they feed into.

WEEKLY_AVAILABILITY ('WA') is the focus here. It replaces the TIME_AVAILABILITY +
DAY_AVAILABILITY pair with one 7x24 grid, stores UTC hour-of-week ints in a JSON
field rather than Choice rows, and OVERRIDES TA/DY when both are present.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.test import TestCase
from django.urls import reverse

from the_gatehouse.models import Profile, PlayerSchedule
from the_gatehouse.signals import handle_image_resize, user_logged_in_handler
from the_tavern.models import Answer, Question, Survey, SurveyResponse


class _SurveyTestBase(TestCase):
    """Fixtures shared by the WA suites, plus the signal disconnects the other
    apps' suites use (force_login's handler builds absolute URLs and enqueues
    Discord work that a bare test request cannot support)."""

    def setUp(self):
        super().setUp()
        user_logged_in.disconnect(user_logged_in_handler)
        self.addCleanup(user_logged_in.connect, user_logged_in_handler)
        post_save.disconnect(handle_image_resize, sender=Profile)
        self.addCleanup(post_save.connect, handle_image_resize, sender=Profile)

        self.user = User.objects.create_user(username='wauser', password='pw')
        self.profile = self.user.profile
        self.profile.timezone = 'UTC'
        # survey_take_view is @player_onboard_required, which redirects away
        # before the view runs unless the profile is both a player and onboarded.
        # `player` is derived from `group`, so set the field behind it.
        self.profile.group = 'P'
        self.profile.player_onboard = True
        self.profile.save(update_fields=['timezone', 'group', 'player_onboard'])

    def _survey(self, **kwargs):
        kwargs.setdefault('title', 'Availability Survey')
        kwargs.setdefault('is_public', True)
        kwargs.setdefault('is_active', True)
        return Survey.objects.create(**kwargs)

    def _wa_question(self, survey, required=False, order=1, text='When are you free?'):
        return Question.objects.create(
            survey=survey, text=text,
            question_type=Question.QuestionType.WEEKLY_AVAILABILITY,
            order=order, required=required)

    def _ta_question(self, survey, order=2):
        question = Question.objects.create(
            survey=survey, text='Which hours?',
            question_type=Question.QuestionType.TIME_AVAILABILITY,
            order=order, required=False,
            ta_enabled_days=['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'])
        question.create_utc_hour_choices()
        return question

    def _dy_question(self, survey, order=3):
        question = Question.objects.create(
            survey=survey, text='Which days?',
            question_type=Question.QuestionType.DAY_AVAILABILITY,
            order=order, required=False)
        question.create_day_choices()
        return question

    def _response(self, survey, offset=0):
        return SurveyResponse.objects.create(
            survey=survey, profile=self.profile, timezone_offset_hours=offset)


class WeeklyAvailabilityAnswerTests(_SurveyTestBase):
    """Answer.clean()'s WA arm. Without it the chain's final `else` raises
    "Unsupported question type." and every WA answer fails validation."""

    def setUp(self):
        super().setUp()
        self.survey = self._survey()
        self.question = self._wa_question(self.survey)
        self.response = self._response(self.survey)

    def _answer(self, hours, **kwargs):
        return Answer(response=self.response, question=self.question,
                      availability_hours=hours, **kwargs)

    def test_a_valid_list_of_hours_is_accepted(self):
        answer = self._answer([0, 14, 167])
        answer.full_clean(exclude=['response', 'question'])
        answer.save()
        self.assertEqual(Answer.objects.get(pk=answer.pk).availability_hours,
                         [0, 14, 167])

    def test_an_empty_list_is_accepted_when_not_required(self):
        """"Free at no hour" is a real answer, not a missing one."""
        self._answer([]).full_clean(exclude=['response', 'question'])

    def test_an_empty_list_is_refused_when_required(self):
        self.question.required = True
        self.question.save(update_fields=['required'])
        with self.assertRaises(ValidationError):
            self._answer([]).full_clean(exclude=['response', 'question'])

    def test_a_non_list_is_refused(self):
        with self.assertRaises(ValidationError):
            self._answer({'mon': [1]}).full_clean(exclude=['response', 'question'])

    def test_a_boolean_is_refused(self):
        """bool subclasses int, so JSON `true` would otherwise pass as hour 1.
        Reachable from a crafted POST, not just in theory."""
        with self.assertRaises(ValidationError):
            self._answer([True]).full_clean(exclude=['response', 'question'])

    def test_out_of_range_hours_are_refused(self):
        for bad in (-1, 168, 999):
            with self.subTest(hour=bad):
                with self.assertRaises(ValidationError):
                    self._answer([bad]).full_clean(exclude=['response', 'question'])

    def test_duplicate_hours_are_refused(self):
        with self.assertRaises(ValidationError):
            self._answer([5, 5]).full_clean(exclude=['response', 'question'])

    def test_a_text_answer_alongside_hours_is_refused(self):
        with self.assertRaises(ValidationError):
            self._answer([1], text_answer='hello').full_clean(
                exclude=['response', 'question'])

    def test_the_display_value_collapses_runs(self):
        """This feeds the CSV export, so a full week must not become 168
        comma-separated integers in one cell."""
        answer = self._answer([18, 19, 20, 42])
        answer.save()
        display = answer.get_display_value()
        self.assertIn('Mon 18:00-21:00', display)
        self.assertIn('Tue 18:00', display)
        self.assertIn('UTC', display)

    def test_the_display_value_of_an_empty_answer(self):
        answer = self._answer([])
        answer.save()
        self.assertEqual(answer.get_display_value(), 'No answer')


class WeeklyAvailabilityOverrideTests(_SurveyTestBase):
    """WA replaces the TA+DY pair rather than combining with it: it already
    describes all 168 hours, so mixing them would add hours nobody chose."""

    def setUp(self):
        super().setUp()
        self.survey = self._survey()
        self.response = self._response(self.survey)

    def _answer_ta(self, question, hours):
        answer = Answer.objects.create(response=self.response, question=question)
        for hour in hours:
            answer.selected_choices.add(question.choices.get(text=str(hour)))
        return answer

    def _answer_dy(self, question, days):
        answer = Answer.objects.create(response=self.response, question=question)
        for day in days:
            answer.selected_choices.add(question.choices.get(text=day))
        return answer

    def test_wa_wins_over_ta_and_dy(self):
        wa = self._wa_question(self.survey)
        ta = self._ta_question(self.survey)
        Answer.objects.create(response=self.response, question=wa,
                              availability_hours=[100, 101])
        self._answer_ta(ta, [14])

        self.assertEqual(self.response.get_combined_availability_hours(),
                         {100, 101})

    def test_an_empty_wa_answer_still_suppresses_ta(self):
        """THE trap. get_combined_availability_hours used to early-return an
        empty set when there were no TA hours, and the override is gated on a WA
        answer EXISTING rather than being non-empty -- otherwise "free at no
        hour" silently falls back to whatever TA said."""
        wa = self._wa_question(self.survey)
        ta = self._ta_question(self.survey)
        Answer.objects.create(response=self.response, question=wa,
                              availability_hours=[])
        self._answer_ta(ta, [14])

        self.assertEqual(self.response.get_combined_availability_hours(), set())

    def test_an_unanswered_wa_question_falls_through_to_ta(self):
        """Proves the null-vs-[] distinction: a WA question with no Answer row
        must not suppress anything."""
        self._wa_question(self.survey)
        ta = self._ta_question(self.survey)
        self._answer_ta(ta, [14])

        hours = self.response.get_combined_availability_hours()
        self.assertIn(14, hours)

    def test_several_wa_answers_union(self):
        """Matches how several TA questions already combine. "Last wins" would
        depend on Answer ordering and change silently on a reorder."""
        first = self._wa_question(self.survey, order=1, text='Weekdays?')
        second = self._wa_question(self.survey, order=2, text='Weekends?')
        Answer.objects.create(response=self.response, question=first,
                              availability_hours=[1, 2])
        Answer.objects.create(response=self.response, question=second,
                              availability_hours=[2, 3])

        self.assertEqual(self.response.get_combined_availability_hours(),
                         {1, 2, 3})

    def test_the_ta_and_dy_path_is_unchanged(self):
        """The docstring's own worked example, pinned so the WA branch above
        cannot quietly alter surveys that predate it."""
        ta = self._ta_question(self.survey)
        dy = self._dy_question(self.survey)
        self._answer_ta(ta, [14, 10])
        self._answer_dy(dy, ['Monday', 'Wednesday'])

        hours = self.response.get_combined_availability_hours()
        self.assertTrue(hours)
        self.assertTrue(all(h // 24 in (0, 2) for h in hours))


class WeeklyAvailabilitySubmissionTests(_SurveyTestBase):
    """The whole path: form -> view -> Answer -> PlayerSchedule.

    These are the tests that matter most. A unit test that builds an Answer by
    hand passes whether or not the view creates one, so only a real POST proves
    the empty-grid case works.
    """

    def setUp(self):
        super().setUp()
        from the_warroom.models import Tournament
        self.tournament = Tournament.objects.create(name='WA Cup', designer=self.profile)
        self.survey = self._survey(series=self.tournament)
        self.question = self._wa_question(self.survey)
        self.url = reverse('survey-take', kwargs={'slug': self.survey.slug})
        self.client.force_login(self.user)

    def _post(self, hours, tz='UTC'):
        return self.client.post(self.url, {
            f'question_{self.question.id}': ','.join(str(h) for h in hours),
            f'question_{self.question.id}_timezone': tz,
            'timezone_offset_hours': '0',
        })

    def test_a_submission_stores_hours_and_writes_the_schedule(self):
        self._post([10, 11, 12])

        answer = Answer.objects.get(question=self.question)
        self.assertEqual(answer.availability_hours, [10, 11, 12])
        schedule = PlayerSchedule.objects.get(
            profile=self.profile, tournament=self.tournament)
        self.assertEqual(schedule.available_hours, [10, 11, 12])

    def test_local_hours_are_converted_to_utc(self):
        """The grid is painted locally; the server converts using ZoneInfo, so
        DST and half-hour zones are the zone's problem and not ours."""
        self._post([14], tz='America/New_York')

        answer = Answer.objects.get(question=self.question)
        # Monday 14:00 in New York (UTC-5 on the reference week) is 19:00 UTC.
        self.assertEqual(answer.availability_hours, [19])

    def test_an_empty_grid_clears_the_schedule(self):
        """The case the `if answer_data:` guard used to swallow: with no Answer
        row created, the override never fires and an emptied grid silently kept
        the old schedule."""
        PlayerSchedule.objects.create(
            profile=self.profile, tournament=self.tournament,
            available_hours=[1, 2, 3])

        self._post([])

        answer = Answer.objects.get(question=self.question)
        self.assertEqual(answer.availability_hours, [])
        schedule = PlayerSchedule.objects.get(
            profile=self.profile, tournament=self.tournament)
        self.assertEqual(schedule.available_hours, [])

    def test_an_invalid_timezone_falls_back_rather_than_erroring(self):
        response = self._post([10], tz='Not/AZone')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Answer.objects.get(question=self.question).availability_hours,
                         [10])

    def test_a_survey_with_no_availability_questions_writes_nothing(self):
        """Guards the other direction: this runs on every submission for a
        series survey, so an unrelated poll must not clear a schedule."""
        PlayerSchedule.objects.create(
            profile=self.profile, tournament=self.tournament,
            available_hours=[1, 2, 3])
        other = self._survey(title='Opinions', series=self.tournament)
        Question.objects.create(survey=other, text='Thoughts?',
                                question_type=Question.QuestionType.OPEN_ENDED,
                                order=1, required=False)

        self.client.post(reverse('survey-take', kwargs={'slug': other.slug}),
                         {f'question_{other.questions.first().id}': 'hi',
                          'timezone_offset_hours': '0'})

        schedule = PlayerSchedule.objects.get(
            profile=self.profile, tournament=self.tournament)
        self.assertEqual(schedule.available_hours, [1, 2, 3])

    def test_the_grid_renders_on_the_take_page(self):
        response = self.client.get(self.url)
        body = response.content.decode()
        self.assertEqual(body.count('class="avail-cell"'), 168)
        self.assertIn('js-availability-grid', body)
        self.assertIn(f'wa_grid_{self.question.id}', body)


class WeeklyAvailabilityBuilderTests(_SurveyTestBase):
    """WA has no Choice rows, which is what makes the builder's TA/DY special
    cases irrelevant to it."""

    def test_a_wa_question_needs_no_choices(self):
        survey = self._survey()
        question = self._wa_question(survey)
        question.full_clean(exclude=['survey', 'section'])
        self.assertEqual(question.choices.count(), 0)

    def test_the_post_save_signal_creates_no_choices_for_wa(self):
        """The signal that fills TA with 24 hours and DY with 7 days must ignore
        WA -- 168 Choice rows per question is exactly what the JSON field avoids."""
        survey = self._survey()
        question = self._wa_question(survey)
        question.save()
        self.assertEqual(question.choices.count(), 0)

    def test_a_survey_with_wa_reports_having_availability_questions(self):
        survey = self._survey()
        self._wa_question(survey)
        self.assertTrue(survey.has_availability_questions())


class TimeAvailabilityConversionTests(_SurveyTestBase):
    """TA choices are named by their UTC hour, and the take-survey grid relabels
    them to local for DISPLAY only. The server therefore must not convert them
    again -- doing so applied the offset twice and stored availability at the
    wrong time of day entirely.
    """

    def _respond(self, offset, hours, days=None, enabled_days=None):
        """A response selecting TA `hours` (choice text = UTC hour) and, if given,
        DY `days`. Returns the combined hour-of-week set."""
        survey = self._survey()
        ta = self._ta_question(survey)
        if enabled_days is not None:
            ta.ta_enabled_days = enabled_days
            ta.save(update_fields=['ta_enabled_days'])
        response = self._response(survey, offset=offset)

        ta_answer = Answer.objects.create(response=response, question=ta)
        for hour in hours:
            ta_answer.selected_choices.add(ta.choices.get(text=str(hour)))

        if days is not None:
            dy = self._dy_question(survey)
            dy_answer = Answer.objects.create(response=response, question=dy)
            for day in days:
                dy_answer.selected_choices.add(dy.choices.get(text=day))
        return response.get_combined_availability_hours()

    def test_midnight_in_utc_minus_seven_is_not_stored_as_two_pm(self):
        """The reported bug, from response 147. The grid showed 12am for the
        slot named '7' (07:00 UTC), and picking it stored 14:00 UTC -- the
        offset had been applied on the way in AND on the way out."""
        hours = self._respond(-7, ['7'], days=['Tuesday', 'Thursday', 'Saturday'])

        self.assertEqual(hours, {31, 79, 127})
        self.assertNotEqual(hours, {38, 86, 134})
        # Every stored hour is 07:00 UTC, which is midnight in UTC-7.
        self.assertTrue(all(h % 24 == 7 for h in hours))

    def test_the_offset_is_applied_once_not_twice(self):
        """Two players who picked the same DISPLAYED hour are free at the same
        instant, so they must land on the same hour-of-week."""
        # In UTC+0 the slot showing midnight is '0'; in UTC-7 it is '7'.
        utc_player = self._respond(0, ['0'], days=['Tuesday'])
        west_player = self._respond(-7, ['7'], days=['Tuesday'])

        self.assertEqual(utc_player, {24})        # Tue 00:00 UTC
        self.assertEqual(west_player, {31})       # Tue 07:00 UTC
        # Different instants, correctly -- midnight local is not the same moment.
        self.assertNotEqual(utc_player, west_player)

    def test_an_evening_hour_carries_into_the_next_utc_day(self):
        """Tue 8pm in UTC-7 IS Wed 03:00 UTC. Storing it as Tue 03:00 would
        round-trip fine for this player but make them appear free 17 hours from
        when they are -- and PlayerSchedule hours are intersected across
        players in different zones."""
        hours = self._respond(-7, ['3'], days=['Tuesday'])

        self.assertEqual(hours, {51})             # Wed 03:00 UTC
        self.assertNotEqual(hours, {27})          # NOT Tue 03:00 UTC

    def test_an_early_hour_carries_back_to_the_previous_utc_day(self):
        """Positive offsets carry the other way; only this direction catches a
        sign error in the carry."""
        # In UTC+2 the slot named '23' displays as 01:00 local.
        hours = self._respond(2, ['23'], days=['Tuesday'])

        self.assertEqual(hours, {23})             # Mon 23:00 UTC

    def test_without_a_day_question_the_hour_fans_across_enabled_days(self):
        hours = self._respond(0, ['7'], enabled_days=['mon', 'wed'])

        self.assertEqual(hours, {7, 55})          # Mon and Wed, 07:00 UTC

    def test_a_missing_timezone_offset_is_treated_as_utc(self):
        """Five live responses have a null offset; it must not raise."""
        hours = self._respond(None, ['7'], days=['Tuesday'])

        self.assertEqual(hours, {31})

    def test_weekly_availability_is_unaffected(self):
        """WA converts with ZoneInfo on submit and never touches this path."""
        survey = self._survey()
        wa = self._wa_question(survey)
        response = self._response(survey, offset=-7)
        Answer.objects.create(response=response, question=wa,
                              availability_hours=[31, 79])

        self.assertEqual(response.get_combined_availability_hours(), {31, 79})


class SurveyTimezoneNameTests(_SurveyTestBase):
    """TA hours convert through ZoneInfo using the zone recorded at submission.

    A fixed numeric offset describes ONE instant, so storing a summer answer at
    -7 (PDT) and redisplaying it against the January reference week at -8 (PST)
    shifted every hour an hour early. The zone name fixes that; the offset is
    kept only for responses recorded before it existed.
    """

    def _ta_response(self, hours, days, tz_name=None, offset=None):
        survey = self._survey()
        ta = self._ta_question(survey)
        dy = self._dy_question(survey)
        response = SurveyResponse.objects.create(
            survey=survey, profile=self.profile,
            timezone_offset_hours=offset, timezone_name=tz_name)

        ta_answer = Answer.objects.create(response=response, question=ta)
        for hour in hours:
            ta_answer.selected_choices.add(ta.choices.get(text=str(hour)))
        dy_answer = Answer.objects.create(response=response, question=dy)
        for day in days:
            dy_answer.selected_choices.add(dy.choices.get(text=day))
        return response

    def test_a_summer_answer_survives_the_winter_reference_week(self):
        """The reported bug. In UTC-7 the slots displaying 4/5/6pm are the UTC
        choices 23, 0 and 1. Converting those through America/Los_Angeles must
        round-trip to 4/5/6pm, not the 3/4/5pm a frozen -7 offset produced."""
        from the_gatehouse.services.availability import utc_to_local_hours

        response = self._ta_response(
            ['23', '0', '1'], ['Tuesday'],
            tz_name='America/Los_Angeles', offset=-7)
        hours = sorted(response.get_combined_availability_hours())

        local = sorted(utc_to_local_hours(hours, 'America/Los_Angeles'))
        self.assertEqual([h % 24 for h in local], [16, 17, 18])
        self.assertTrue(all(h // 24 == 1 for h in local))   # still Tuesday

    def test_the_recorded_zone_beats_the_profile_default(self):
        """Where the respondent WAS outranks a default they set long ago."""
        self.profile.timezone = 'UTC'
        self.profile.save(update_fields=['timezone'])
        response = self._ta_response(['12'], ['Tuesday'],
                                     tz_name='America/Los_Angeles')

        self.assertEqual(response.resolve_timezone_name(), 'America/Los_Angeles')

    def test_a_recorded_offset_beats_the_profile_default(self):
        """A legacy response still carries evidence about where the respondent
        was. Preferring the profile would silently discard it -- which produced
        a whole-timezone error, not merely an hour."""
        self.profile.timezone = 'UTC'
        self.profile.save(update_fields=['timezone'])
        response = self._ta_response(['7'], ['Tuesday'], offset=-7)

        self.assertIsNone(response.resolve_timezone_name())
        self.assertEqual(response.get_combined_availability_hours(), {31})

    def test_a_response_with_neither_falls_back_to_the_profile(self):
        self.profile.timezone = 'America/Los_Angeles'
        self.profile.save(update_fields=['timezone'])
        response = self._ta_response(['12'], ['Tuesday'])

        self.assertEqual(response.resolve_timezone_name(), 'America/Los_Angeles')

    def test_a_zone_without_dst_is_stable(self):
        """Phoenix never shifts, so the stored hour is the same whenever the
        response was submitted -- unlike Los Angeles, where the same wall clock
        maps to a different UTC hour in summer and winter."""
        from the_gatehouse.services.availability import utc_to_local_hours

        # offset -7 makes the slot named '23' display as 16:00 local.
        response = self._ta_response(['23'], ['Tuesday'],
                                     tz_name='America/Phoenix', offset=-7)
        hours = sorted(response.get_combined_availability_hours())
        local = sorted(utc_to_local_hours(hours, 'America/Phoenix'))

        self.assertEqual([h % 24 for h in local], [16])

    def test_weekly_availability_is_untouched(self):
        survey = self._survey()
        wa = self._wa_question(survey)
        response = SurveyResponse.objects.create(
            survey=survey, profile=self.profile,
            timezone_name='America/Los_Angeles')
        Answer.objects.create(response=response, question=wa,
                              availability_hours=[48, 49])

        self.assertEqual(response.get_combined_availability_hours(), {48, 49})


class SurveyProfileTimezoneTests(_SurveyTestBase):
    """A survey submission seeds a BLANK profile timezone, so a player who never
    visits /availability still gets their hours shown in their own time."""

    def setUp(self):
        super().setUp()
        self.profile.timezone = None
        self.profile.save(update_fields=['timezone'])
        self.survey = self._survey()
        self.wa = self._wa_question(self.survey)
        self.url = reverse('survey-take', kwargs={'slug': self.survey.slug})
        self.client.force_login(self.user)

    def _tz_question(self):
        """A timezone question shaped like the live one: an MC whose choice text
        carries the IANA name in parentheses."""
        from the_tavern.models import Choice
        question = Question.objects.create(
            survey=self.survey, text='What is your timezone?',
            question_type=Question.QuestionType.MULTIPLE_CHOICE,
            order=5, required=False)
        return question, Choice.objects.create(
            question=question, text='US Pacific — PT (America/Los_Angeles)', order=0)

    def _post(self, extra=None):
        data = {
            f'question_{self.wa.id}': '10',
            f'question_{self.wa.id}_timezone': 'UTC',
            'timezone_offset_hours': '-7',
            'timezone_name': 'America/Denver',
        }
        data.update(extra or {})
        return self.client.post(self.url, data)

    def test_a_blank_profile_timezone_is_set_from_the_browser(self):
        self._post()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/Denver')

    def test_a_timezone_question_beats_the_browser(self):
        """A deliberate answer outranks detection -- someone answering from a
        hotel should not have their profile rewritten to the hotel's zone."""
        question, choice = self._tz_question()
        self._post({f'question_{question.id}': str(choice.id)})

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'America/Los_Angeles')

    def test_an_existing_profile_timezone_is_never_overwritten(self):
        self.profile.timezone = 'Europe/Berlin'
        self.profile.save(update_fields=['timezone'])

        self._post()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, 'Europe/Berlin')

    def test_the_response_records_the_zone_it_was_taken_in(self):
        self._post()

        response = SurveyResponse.objects.get(survey=self.survey)
        self.assertEqual(response.timezone_name, 'America/Denver')
