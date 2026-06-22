# Root Database (RDB)

A fan database website for the board game [Root](https://ledergames.com/products/root-a-game-of-woodland-might-and-right). Tracks official and fan-created game content, gameplay records, rules, faqs, and tournament statistics.

## Features

- **Content Repository**: Browse and search official and fan-made factions, maps, decks, hirelings, vagabonds, landmarks, house rules, and more
- **Game Records**: Log games - factions, players, points, and optional turn-by-turn scorecards
- **Tournaments**: Organize and track tournaments with stages, rounds, and leaderboards
- **Discord Integration**: Log in via Discord OAuth; guild membership gates certain features
- **Resources**: Search links to PNP resources to create new fan content
- **Community**: Submit fan content for review with links to Discord guilds, create surveys for tournament registration and feedback

## Tech Stack

- **Backend**: Django 5.1, PostgreSQL
- **Task Queue**: Celery + Redis
- **Auth**: Discord OAuth
- **Frontend**: HTMX

## Apps

| App | Purpose |
|---|---|
| `the_gatehouse` | User profiles, site config, themes, Discord guild integration, notifications, analytics |
| `the_keep` | Content repository — factions, maps, decks, hirelings, vagabonds, landmarks, rules/FAQs, PNP assets |
| `the_warroom` | Games, Scorecards, Series/Tournamentsg |
| `the_tavern` | Surveys |
| `the_forge` | Create pdf/pnd/json content |

