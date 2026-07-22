"""
Reusable helpers for Discord message components (buttons, string selects) and
the interaction-response types that go with them. The bot is stateless, so state
is threaded through component custom_ids and recovered from the message's own
component state (Discord echoes it back on every component interaction).
"""

# Interaction-response types used with components.
RESPONSE_UPDATE_MESSAGE = 7            # edit the component's own message in place
RESPONSE_DEFERRED_UPDATE_MESSAGE = 6  # ack without a visible edit (unused here; documented)

# Component + button style constants.
COMPONENT_ACTION_ROW = 1
COMPONENT_BUTTON = 2
COMPONENT_STRING_SELECT = 3
STYLE_PRIMARY, STYLE_SECONDARY, STYLE_SUCCESS, STYLE_DANGER = 1, 2, 3, 4


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
    return {
        "type": COMPONENT_STRING_SELECT, "custom_id": custom_id,
        "placeholder": placeholder, "min_values": min_values,
        # Discord requires max_values >= 1 even when min_values is 0.
        "max_values": max(1, max_values), "options": options[:25],  # option cap is 25
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
