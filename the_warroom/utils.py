import re
from better_profanity import profanity
profanity.load_censor_words()

def clean_nickname(raw_title):
    nickname = (raw_title or '').strip()
    lower_nickname = nickname.lower()

    # Block completely if link or Imported game
    blocked_substrings = ['https://', 'discord.com', 'import game 20']
    if any(substring in lower_nickname for substring in blocked_substrings):
        return None

    # # List of substrings to remove (case-insensitive)
    # substrings_to_remove = [' async ', 
    #                         ' ep ', ' e&p ', 'ep deck', 'e&p deck', 'base deck'
    #                         ' rc ', 'random clearings', 'random clearing '
    #                         ' 1vb ', 'ban second vg', 'ban second vb', 'ban 2nd', 'vb'
    #                         'live nt', ' nt ', 'live with timer'
    #                         ]
    
    # # Remove each substring (case-insensitive)
    # for substring in substrings_to_remove:
    #     nickname = re.sub(re.escape(substring), ' ', nickname, flags=re.IGNORECASE)
    
    # # Clean up extra whitespace
    # nickname = ' '.join(nickname.split()).strip()

    # If nickname contains profanity, censor it
    if profanity.contains_profanity(nickname):
        nickname = profanity.censor(nickname)

    return nickname[:50]  # Truncate to 50 characters