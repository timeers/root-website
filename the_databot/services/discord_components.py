"""
Reusable helpers for Discord message components (buttons, string selects) and
the interaction-response types that go with them. The bot is stateless, so state
is threaded through component custom_ids and recovered from the message's own
component state (Discord echoes it back on every component interaction).
"""

# Interaction-response types used with components.
RESPONSE_UPDATE_MESSAGE = 7            # edit the component's own message in place
RESPONSE_DEFERRED_UPDATE_MESSAGE = 6  # ack without a visible edit
RESPONSE_MODAL = 9                     # open a modal in response to a component click

# Component + button style constants.
COMPONENT_ACTION_ROW = 1
COMPONENT_BUTTON = 2
COMPONENT_STRING_SELECT = 3
COMPONENT_TEXT_INPUT = 4
COMPONENT_LABEL = 18
STYLE_PRIMARY, STYLE_SECONDARY, STYLE_SUCCESS, STYLE_DANGER = 1, 2, 3, 4
TEXT_INPUT_SHORT, TEXT_INPUT_PARAGRAPH = 1, 2


# ── Builders ───────────────────────────────────────────────────────────────
def action_row(*components):
    return {"type": COMPONENT_ACTION_ROW, "components": list(components)}


def button(label, custom_id, style=STYLE_PRIMARY, emoji=None):
    comp = {"type": COMPONENT_BUTTON, "style": style, "label": label, "custom_id": custom_id}
    if emoji:
        comp["emoji"] = emoji
    return comp


def select_option(label, value, emoji=None, default=False):
    opt = {"label": label[:100], "value": value, "default": default}
    if emoji:
        opt["emoji"] = emoji
    return opt


def string_select(custom_id, options, placeholder="", min_values=0, max_values=1):
    options = options[:25]  # Discord caps a select at 25 options
    return {
        "type": COMPONENT_STRING_SELECT, "custom_id": custom_id,
        "placeholder": placeholder, "min_values": min_values,
        # Discord requires 1 <= max_values <= number of options. Clamp against the
        # capped option count so a caller passing len(pre-cap options) can't send a
        # max_values that exceeds the options actually included (a 400).
        "max_values": max(1, min(max_values, len(options))), "options": options,
    }


def text_input(custom_id, style=TEXT_INPUT_SHORT, value="", required=True,
               max_length=None, placeholder=None):
    """The interactive input nested inside a Label component -- NOT wrapped in an
    Action Row. Discord deprecated Action-Row-wrapped text inputs in modals
    (Aug 2025 changelog) in favor of Label; this codebase has no prior modal code
    to stay compatible with, so it targets the current shape from the start."""
    comp = {
        "type": COMPONENT_TEXT_INPUT, "custom_id": custom_id,
        "style": style, "required": required,
        "value": (value or "")[:4000],  # Discord caps the prefilled value at 4000 chars
    }
    if max_length:
        comp["max_length"] = max_length
    if placeholder:
        comp["placeholder"] = placeholder[:100]
    return comp


def label_component(label, component, description=None):
    """Wrap one interactive component (a text_input, string_select, etc.) with the
    label/description text Discord now requires for it to render inside a modal."""
    comp = {"type": COMPONENT_LABEL, "label": label[:45], "component": component}
    if description:
        comp["description"] = description[:100]
    return comp


def modal(custom_id, title, *labeled_components):
    """A MODAL (type 9) response body: {"custom_id", "title", "components"}. Each
    entry in `labeled_components` should already be a label_component(...) --
    modals no longer take Action Rows at the top level for text inputs."""
    return {
        "custom_id": custom_id,
        "title": title[:45],          # Discord caps a modal title at 45 chars
        "components": list(labeled_components),
    }


# ── custom_id codec ──────────────────────────────────────────────────────────
# One convention for all interactive commands: "action:arg1:arg2". The action is
# the dispatch key (COMPONENT_HANDLERS is keyed by it); args carry scalar state
# (custom_id max length is 100 chars, so keep args short — never pack lists here).
def encode_custom_id(action, *args):
    return ":".join([action, *(str(a) for a in args)])


def decode_custom_id(custom_id):
    """('action', ['arg1', 'arg2']) from 'action:arg1:arg2'."""
    parts = custom_id.split(":")
    return parts[0], parts[1:]


# ── Message-state reader ─────────────────────────────────────────────────────
def selected_values(payload, select_custom_id_prefix):
    """Recover a string select's chosen values from a component message by reading
    which options were rendered default=True — needed when a *button* fires the
    interaction (a button press doesn't echo the select's values). Matches the
    select whose custom_id starts with `select_custom_id_prefix`."""
    for row in payload.get("message", {}).get("components", []):
        for comp in row.get("components", []):
            if (comp.get("type") == COMPONENT_STRING_SELECT
                    and comp.get("custom_id", "").startswith(select_custom_id_prefix)):
                return [o["value"] for o in comp.get("options", []) if o.get("default")]
    return []
