# Plan: Add Day Selection to TIME_AVAILABILITY Questions

## Overview
Add the ability for survey creators to specify which days of the week a TIME_AVAILABILITY question applies to. Respondents will see a single time grid with the applicable days displayed as context information.

## Design Decision
- **Storage**: JSONField storing list of enabled day codes (e.g., `['mon', 'tue', 'wed']`)
- **Display**: Single 24-hour time grid with applicable days shown as informational text above
- **Default**: All 7 days enabled

---

## Implementation Steps

### 1. Model Changes
**File**: `the_gatehouse/models.py`

Add module-level constants and default function (before the Question class, around line 1199):

```python
# Day configuration constants for TIME_AVAILABILITY questions
TA_DAY_CODES = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
TA_DAY_LABELS = {
    'mon': 'Monday', 'tue': 'Tuesday', 'wed': 'Wednesday',
    'thu': 'Thursday', 'fri': 'Friday', 'sat': 'Saturday', 'sun': 'Sunday'
}

def get_default_ta_days():
    """Return all days enabled by default for TIME_AVAILABILITY questions"""
    return TA_DAY_CODES.copy()
```

Add field to `Question` model (after line 1240, after `post_selection_mode`):

```python
ta_enabled_days = models.JSONField(
    default=get_default_ta_days,
    blank=True,
    help_text="Days of week for TIME_AVAILABILITY questions"
)
```

Add helper method to `Question` class (after `get_visible_choices` method, around line 1325):

```python
def get_enabled_days_display(self):
    """Return comma-separated list of enabled day names for TIME_AVAILABILITY questions"""
    if self.question_type != self.QuestionType.TIME_AVAILABILITY:
        return ""
    days = self.ta_enabled_days if self.ta_enabled_days else TA_DAY_CODES
    return ", ".join(TA_DAY_LABELS.get(d, d) for d in days)
```

### 2. Migration
```bash
python manage.py makemigrations
python manage.py migrate
```

### 3. Survey Creator UI - Day Selection Checkboxes
**File**: `the_gatehouse/templates/the_gatehouse/surveys/partials/survey_form_scripts.html`

In `handleTypeChange()` function (around line 707), update the TA case to show day checkboxes:

```javascript
if (type === 'TA') {
    const enabledDays = existingData?.ta_enabled_days || ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
    const dayLabels = {
        'mon': 'Mon', 'tue': 'Tue', 'wed': 'Wed',
        'thu': 'Thu', 'fri': 'Fri', 'sat': 'Sat', 'sun': 'Sun'
    };

    const dayCheckboxes = Object.entries(dayLabels).map(([code, label]) => `
        <div class="form-check form-check-inline">
            <input class="form-check-input ta-day-checkbox" type="checkbox"
                   id="ta_day_${questionId}_${code}" value="${code}"
                   ${enabledDays.includes(code) ? 'checked' : ''}>
            <label class="form-check-label" for="ta_day_${questionId}_${code}">${label}</label>
        </div>
    `).join('');

    optionsContainer.innerHTML = `
        <div class="alert alert-info">
            <i class="bi bi-clock"></i> <strong>Time Availability</strong><br>
            <small>Creates 24 hour slots (UTC). Respondents see times in their local timezone.</small>
        </div>
        <div class="mb-3">
            <label class="form-label">Applicable Days <span class="text-danger">*</span></label>
            <div class="ta-days-container">${dayCheckboxes}</div>
            <small class="text-muted">Select which days this question applies to (at least one required).</small>
            <div class="invalid-feedback" id="ta_days_error_${questionId}" style="display: none;">
                Please select at least one day.
            </div>
        </div>
    `;
}
```

In form submission (around line 1516), collect enabled days with validation:

```javascript
if (questionData.type === 'TA') {
    const dayCheckboxes = card.querySelectorAll('.ta-day-checkbox:checked');
    const enabledDays = Array.from(dayCheckboxes).map(cb => cb.value);
    questionData.ta_enabled_days = enabledDays;

    // Validate at least one day is selected
    if (enabledDays.length === 0) {
        const errorDiv = document.getElementById(`ta_days_error_${questionId}`);
        if (errorDiv) {
            errorDiv.style.display = 'block';
        }
        validationErrors.push({
            questionId: questionId,
            message: `Question ${questionData.order}: Please select at least one day for the time availability question.`
        });
    }
}
```

### 4. View Changes - Save/Load Day Configuration
**File**: `the_gatehouse/views.py`

In `survey_edit_view` (around line 3246), include `ta_enabled_days` in question data:

```python
from the_gatehouse.models import TA_DAY_CODES  # Add to imports

q_data = {
    # ... existing fields ...
    'ta_enabled_days': question.ta_enabled_days or TA_DAY_CODES,
}
```

In POST handling for `survey_edit_view` (around line 3070), save enabled days:

```python
if q_data['type'] == 'TA':
    question.ta_enabled_days = q_data.get('ta_enabled_days') or TA_DAY_CODES
```

In POST handling for `create_survey_view` (around line 3152), save enabled days:

```python
if q_data['type'] == 'TA':
    question.ta_enabled_days = q_data.get('ta_enabled_days') or TA_DAY_CODES
```

### 5. Survey Taking UI - Display Applicable Days
**File**: `the_gatehouse/templates/the_gatehouse/surveys/take_survey.html`

Update the TA section (around line 79) to show applicable days:

```html
{% elif question.question_type == 'TA' %}
    <div class="mt-3">
        <div class="alert alert-info" id="timezone_info_{{ question.id }}">
            <i class="bi bi-clock"></i> <span class="timezone-name">Detecting your timezone...</span>
        </div>
        <p class="text-muted small mb-2">
            <i class="bi bi-calendar3"></i> <strong>{% trans 'Applicable days' %}:</strong> {{ question.get_enabled_days_display }}
        </p>
        <p class="text-muted small mb-2">
            <i class="bi bi-check2-square"></i> {% trans 'Select all hours you are available:' %}
        </p>
        <!-- existing time grid code unchanged -->
```

### 6. Survey Preview UI - Display Applicable Days
**File**: `the_gatehouse/templates/the_gatehouse/surveys/survey_preview.html`

Update the TA section (around line 108) to show applicable days:

```html
{% elif question.question_type == 'TA' %}
    <div class="mt-3">
        <div class="alert alert-info">
            <i class="bi bi-clock"></i> {% trans "Times will be shown in respondent's local timezone" %}
        </div>
        <p class="text-muted small mb-2">
            <i class="bi bi-calendar3"></i> <strong>{% trans 'Applicable days' %}:</strong> {{ question.get_enabled_days_display }}
        </p>
        <p class="text-muted small mb-2">
            <i class="bi bi-check2-square"></i> {% trans 'Select all relevant hours (24-hour format shown):' %}
        </p>
        <!-- existing time grid code unchanged -->
```

### 7. Survey Results UI - Display Applicable Days
**File**: `the_gatehouse/templates/the_gatehouse/surveys/survey_results.html`

Update the TA section (around line 55) to show applicable days context:

```html
{% elif question_data.question.question_type == 'TA' %}
    {# Time Availability - show bar chart with timezone conversion #}
    <div class="alert alert-info mb-3">
        <i class="bi bi-clock"></i> {% trans 'Times shown in your timezone' %}: <span id="ta-timezone-{{ question_data.question.id }}"></span>
        <br><small class="text-muted">
            <i class="bi bi-calendar3"></i> {% trans 'Applicable days' %}: {{ question_data.question.get_enabled_days_display }}
        </small>
        <br><small class="text-muted">{% trans 'Sorted by popularity (most selected first)' %}</small>
    </div>
    <!-- existing results code unchanged -->
```

### 8. CSS for Day Selection
**File**: `the_gatehouse/templates/the_gatehouse/surveys/partials/survey_styles.html`

Add styles for day checkboxes in creator UI:

```css
/* Time Availability Day Selection */
.ta-days-container {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.5rem;
    background-color: #f8f9fa;
    border-radius: 0.375rem;
}

.ta-days-container .form-check-inline {
    margin-right: 0;
}
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `the_gatehouse/models.py:1199` | Add `TA_DAY_CODES`, `TA_DAY_LABELS`, `get_default_ta_days()` at module level |
| `the_gatehouse/models.py:1240` | Add `ta_enabled_days` JSONField to Question model |
| `the_gatehouse/models.py:1325` | Add `get_enabled_days_display()` method to Question model |
| `the_gatehouse/templates/.../survey_form_scripts.html:707` | Add day checkboxes in handleTypeChange() |
| `the_gatehouse/templates/.../survey_form_scripts.html:1516` | Collect enabled days on form submit with validation |
| `the_gatehouse/views.py:3246` | Include ta_enabled_days in edit view data |
| `the_gatehouse/views.py:3070` | Save ta_enabled_days in edit POST |
| `the_gatehouse/views.py:3152` | Save ta_enabled_days in create POST |
| `the_gatehouse/templates/.../take_survey.html:79` | Display applicable days text |
| `the_gatehouse/templates/.../survey_preview.html:108` | Display applicable days text |
| `the_gatehouse/templates/.../survey_results.html:55` | Display applicable days in results context |
| `the_gatehouse/templates/.../survey_styles.html` | CSS for day checkboxes |

---

## Verification

1. **Create survey**: Add a TIME_AVAILABILITY question, verify all 7 days are checked by default
2. **Validation**: Try to save with no days selected, verify error message appears
3. **Customize days**: Uncheck some days (e.g., only Mon-Fri), save survey
4. **Edit survey**: Re-open survey, verify day selections persisted correctly
5. **Preview survey**: Verify applicable days text shows in preview
6. **Take survey**: As respondent, verify applicable days text shows correctly above time grid
7. **View results**: Verify applicable days context shows in results page
8. **Backward compatibility**: Existing TA questions should show all 7 days by default
