import re

from the_gatehouse.tasks import send_discord_message_task

from better_profanity import profanity
profanity.load_censor_words()
# # Remove words you don't want censored
# whitelist = ["dummy", "drunk", "fat", "god", 
#              "heck", "hell", "jerk", "junky", "junkie", 
#              "kill", "lmao", "lmfao", "moron", "omg", "pawn", 
#              "pot", "prick", "prude", "rum", "sadism", 
#              "sadist", "screw", "thug", "thrust", "ugly", 
#              "vomit", "weed", "weirdo", "womb"]
# clean_list = profanity.CENSOR_WORDSET - whitelist

# # Reload the profanity filter with the cleaned list
# profanity.load_censor_words(clean_list)

def clean_nickname(raw_title):
    nickname = (raw_title or '').strip()
    lower_nickname = nickname.lower()

    # Block completely if link or Imported game
    blocked_substrings = ['https://', 'discord.com', 'import game 20']
    if any(substring in lower_nickname for substring in blocked_substrings):
        return None

    # # List of substrings to remove (case-insensitive)
    # substrings_to_remove = [' async ', 
    #                         ' ep ', ' e&p ', 'ep deck', 'e&p deck', 'base deck',
    #                         ' rc ', 'random clearings', 'random clearing ',
    #                         ' 1vb ', 'ban second vg', 'ban second vb', 'ban 2nd', ' 1 vb',
    #                         'live nt', ' nt ', 'live with timer', '**live**'
    #                         ]
    
    # # Remove each substring (case-insensitive)
    # for substring in substrings_to_remove:
    #     nickname = re.sub(re.escape(substring), ' ', nickname, flags=re.IGNORECASE)
    
    # Clean up extra whitespace
    nickname = ' '.join(nickname.split()).strip()

    # If nickname contains profanity, censor it
    if profanity.contains_profanity(nickname):
        new_nickname = profanity.censor(nickname)
        send_discord_message_task.delay(f'Nickname "{nickname}" replaced with "{new_nickname}"')
        nickname = new_nickname
        

    return nickname[:50]  # Truncate to 50 characters

def get_round_and_stage(tournament):
    use_stages = tournament.use_stages
    use_rounds = tournament.use_rounds
    if use_stages:
        only_stage = None
        only_round = None
    else:
        only_stage = tournament.stages.first()
        if use_rounds or not only_stage:
            only_round = None
        else:
            only_round = only_stage.rounds.first()
    return only_stage, only_round