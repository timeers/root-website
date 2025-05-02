The goal is to have a website that lists official and unofficial Root content with an easy way to search for any fan related content and record games and track stats.

There will be a list to search Factions, Maps, Decks etc.
This section will have detailed info as well as stats.

There will be another section that lists gameplay details. 
Factions and other components used, score and players.

There will be a third section to list links to community resources.

Users will be able to log in via Discord. 
If they belong to the WW discord server they will be able to record game results. 
They will not be able to edit the results once a game has been submitted. 
An admin will be able to edit or delete game data.

Some users will also be able to input new unofficial content which will be able to be selected in the gameplay section.
These users will be able to update or delete the content they posted.

This django project is divided into three main sections:

the_gatehouse is for website models and user profiles
    -Profile
    -Website
    -Theme
    -Foreground and Background Images
the_keep is for posts (Factions, Maps etc.) and PNP resource links
    -Posts
        -Faction
        -Deck
        -Map
        -Hireling
        -Vagabond
        -Landmark
        -Tweak
    -Expansions
    -Post Translations
    -Pieces
the_warroom is for Games, Series and Scorecards
    -Efforts
    -Games
    -Tournaments
    -Rounds
    -Scorecards
    -Turns
Todo:
Allow players to be added to game after submission.
Fix bug in Scorecards where you cannot delete a turn once saved.
