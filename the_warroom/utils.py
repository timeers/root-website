
from better_profanity import profanity
profanity.load_censor_words()

def clean_nickname(raw_title):
    nickname = (raw_title or '').strip()
    lower_nickname = nickname.lower()

    # Block completely if link or Imported game
    blocked_substrings = ['https://', 'discord.com', 'import game 20']
    if any(substring in lower_nickname for substring in blocked_substrings):
        return None

    # If nickname contains profanity, censor it
    if profanity.contains_profanity(nickname):
        nickname = profanity.censor(nickname)

    return nickname[:50]  # Truncate to 50 characters