# Changelog

## [1.10.0] - 2026-1-8 Happy New Year!

Users can now submit new Factions to the website! Submitted Factions will need to be reviewed by an admin before they are made public. Knave Captains - starting items and abilities can now be added to Vagabonds. Users can also add detailed card images for their Faction's cards.

### New Features
- Knave Captains
- Linked Law entries for Vagabonds and Maps to Component page
- Decks and Cards
- Changelog
- Designers can now allow co-designers to make changes
- Logged in users can submit posts for approval
- Add player names to submitted games

### Improvements
- Component urls are more strict (factions/autumn will not pull up Autumn Map)
- Validation on Component forms
- Dominance Card Image to Scorecards
- Certain image urls are now stable addresses with timestamps for caching
- Recent posts dropdown now includes all posts you can edit (designer or co-designer with edit authorized)
- Added Faction board back to Clockwork Factions
- Captains tab added to Advanced Search
- Register new players more easily during Game submission

### Bug Fixes
- Designer names display properly in Universal Search
- Spacing on Register Player form
- Universal Search htmx response error on Record Game page
- FAQ Post Title and Reference Laws causing extra DB queries
- Co-designers now update on save
- Clockwork only games will now display as Playtests
- Users imported from RDL without standard +0000 are standardized
- Fixed RDL imports to include Undrafted VB, Landmarks and Hirelings

### Known Issues
- Unable to remove turns on Scorecard once saved
- Games page slow to load


## [1.9.3] - 2025-12-7 Co-Designers

Factions can now list co-designers.

### New Features
- Added co-designers for Posts
- Versions added to Post Page

### Improvements
- Linebreaks for FAQs

### Bug Fixes
- Display Name bug on login


## [1.9.2] - 2025-11-29 Expansion Laws

Expansions now have links to all related Laws and FAQs in one place.

### New Features
- Added Law and FAQ to Expansions
- Added French Root invite when logged in
- Copy law text button

### Improvements
- Expansion page improvements


## [1.9.1] - 2025-11-1 Rules

Law updates are now pulled from Leder Rules Library automatically to keep the Law in sync.

### New Features
- Added Rules Files for Laws
- Sync Avatar with Discord
- Filter FAQs by Official/Fan

### Improvements
- Advanced Search improvements
- Added Law and FAQ tabs to header
- Improved Law Search
- Added feedback links for Laws and FAQs

### Bug Fixes
- Limit game nicknames to 50 characters


## [1.9.0] - 2025-10-19 Root Digital League

Games recorded in the Root Digital Leage website are now imported periodically.

### New Features
- Added Celery and Celery Beat for tasks
- Added RDL Game imports with Pliskin's Match API

### Improvements
- Advanced Search Improvements
- Discord Webhook for Automations


## [1.8.1] - 2025-9-29 Advanced Fixes

### New Features
- More filters for Advanced Search

### Improvements
- Advanced Search meta for link sharing
- Advanced Search formatting improvements

### Bug Fixes
- Integer error in Advanced Search


## [1.8.0] - 2025-9-25 Advanced Search

### New Features
- Advanced Search Pages

### Improvements
- River Clearings, Building Slots and Ruins added to Maps

### Bug Fixes
- Tournament player count fix

## [1.7.4] - 2025-8-19 Newcomers

### Improvements
- Added law icons for new factions
- Tournament meta data and picture added


## [1.7.3] - 2025-7-21 Into the Forest

### New Features
- Forests added for Maps
- Added German
- Language specific links for PNP and TTS

### Improvements
- Added wrappable text for long links

### Bug Fixes
- Removed Clockwork from faction leaderboard
- Added ID to post sorting to prevent duplicates


## [1.7.2] - 2025-6-18 Seyria Prep

### New Features
- Yaml upload for Laws
- Download Law as Yaml file

### Improvements
- Added plain text for Law Title and Description
- Changed small caps to ** and italics to _ to match Seyria yaml
- Uploaded Weird Root playtest data


## [1.7.1] - 2025-5-27 Law Links

### New Features
- Link to faction by clicking Faction icon in Law
- Copy link button added to Law
- Table of Contents for Law
- Reference laws added to FAQs
- Edit and Delete page for Laws
- Added Laws to Universal Search

### Improvements
- Added meta to Law pages
- Added ability to mark laws as private for in progress laws

### Bug Fixes
- Law formatting
- Reworked how languages apply to laws
- Smallcaps fix

## [1.7.0] - 2025-5-23 The Law of Root

### New Features
- Added Laws
- Added FAQs

### Bug Fixes
- Animal match updated for animals with "and" in name

## [1.6.3] - 2025-4-29 Kyle Ferrin

### New Features
- Added Kyle Ferrin as a separate field so that he can be credited
- Record game from tournament page if you are registered
- Rootjam link added

### Improvements
- Status page shows images for official Factions, Decks and Maps
- Filter games by Official components only
- Vagabond lists other pieces

### Bug Fixes
- Error display for Post forms


## [1.6.2] - 2025-4-24 Artwork makes the Heart Work

### New Features
- Original Artwork by Tin (the Foil)


## [1.6.1] - 2025-4-19 Small Fixes

### New Features
- In Progress page for Games and Scorecards
- Added Russian and Dutch
- Click image to view full screen
- Added Holiday Themes

### Improvements
- Stable check improvements
- Translated title shown on Leaderboards

### Bug Fixes
- Added missing static tags
- Typo in small translation board

## [1.6.0] - 2025-4-5 Translations

### New Features
- Added translations
- Added French Root links

### Improvements
- Home page improvements
- Select date when recording game
- Moved to Postgres database

### Bug Fixes
- Game form issue with blank form
- Scorecard average calculation adjusted
- Leaderboard fixes

## [1.5.0] - 2025-3-12 The Themes

### New Features
- Themes added
- Background images
- New homepage
- Status page for components
- Color Groups added for finding all components of a color
- Website Footer

### Improvements
- Recaptcha added for Feedback form
- Searching Vagabond added while loading
- Pieces added to Universal Search

### Bug Fixes
- Fixed player name on player game page

## [1.4.1] - 2025-2-16 Clean Animations

### New Features
- Faction specific Dominance conditions added
- Added recent posts to header dropdown
- Edit game nickname and notes after submission
- Feedback form added

### Improvements
- Added colored underline for Faction cards
- Added hidden descriptions for Component cards
- Report button added to games, factions and users
- Luminari added for titles
- Faction stats redesigned to mimic faction board

### Bug Fixes
- Pinning resources bugs

## [1.4.0] - 2025-2-11 Universal Search

### New Features
- New Universal Search in header
- send_discord_message webhook added

### Improvements
- Leaderboard improvements
- Tournaments improvements


## [1.3.4] - 2025-1-31 Discord Meta

### New Features
- Added meta data for Discord Sharing

### Improvements
- Resource serach page improvements
- Expansion Images
- Fixed HTTPS certificate


## [1.3.3] - 2025-1-15 Color Changes

### Improvements
- Moved color field to Post
- Generic points added to Scorecard
- Player not required for recording games

### Bug Fixes
- Stable status no longer removed when editing
- Save progress button on game form
- Link Scorecard bug with in progress games
- Scorecard nickname bug

## [1.3.2] - 2025-1-9 Thank you Next Scorecard

### New Features
- Added button for Next and Previous Scorecard

### Improvements
- Button Spacing
- Input options for scorecards
- Negative numbers on Scorecards
- Icon category for Resources


## [1.3.1] - 2025-1-2 Resourceful Fixes

### New Features
- Save In Process Games

### Improvements
- Added images for Component links (BGG, TTS, LG)
- Added resources to profiles

### Bug Fixes
- Edit button when logged out
- Max Characters on Category
- Onboard page error

## [1.3.0] - 2024-12-31 PNP Assets

### New Features
- PNP Assets for community links

### Improvements
- Login no longer required to view most pages

## [1.2.3] - 2024-12-28 Stablilty

### New Features
- Stability Check function for Posts
- First user login

### Improvements
- Page Titles
- requirements.txt
- Split out LG Link from WW Link
- Reach and Faction Type Added to Search

### Bug Fixes
- Game Filter Queryset
- Logged out user error

## [1.2.2] - 2024-12-27 Django Rest Framework

### New Features
- Adding Django Rest
- Line Charts for turn by turn game data
- Pie Charts for point categories


## [1.2.1] - 2024-12-25 Christmas Party

### New Features
- Onboarding page
- Player stats page

### Improvements
- Prefetch to reduce database load
- Scorecard linking

### Bug Fixes
- Coalition restrictions for Tournaments

## [1.2.0] - 2024-12-15 Tournaments 

### New Features
- Tournaments added
- Tournament and Round pages

### Improvements
- Dominance added to Scorecards
- Profile filter for Fan Content
- Game tile links directly to Game page

## [1.1.4] - 2024-12-13 Going Live!

### New Features
- Server online

### Known Issues
- Media display issues
- Empty [] displaying in forms

## [1.1.3] - 2024-12-12 Leaderboards

### New Features
- Added Component Leaderboards

### Improvements
- Pieces combined to one model


## [1.1.2] - 2024-12-6 Scorecards

### New Features
- Scorecards and Turns added
- Assign Scorecards to Efforts

### Improvements
- Search by designer or component type

## [1.1.1] - 2024-12-1 Game Form

### New Features
- Register Player form added to Record Game
- Game submission form

### Improvements
- Special Dominance options based on Deck
- Coalition list dynamically created


## [1.1.0] - 2024-11-18 AllAuth

### New Features
- Discord AllAuth for login
- User comments for games and posts
- User bookmarks for games
- User groups Unregistered, Discord Members, Designers and Admin


## [1.0.3] - 2024-11-12 Game Filters

### New Features
- New Game Filter
- Admin Merge Profiles

### Improvements
- Game Page pagination
- Component Detail Page improvements

### Bug Fixes
- Fixed games list not showing all games


## [1.0.2] - 2024-11-07 Profiling

### New Features
- Added profiles to own Posts and participate in games

### Improvements
- Moved files to AWS S3

### Bug Fixes
- Delete, View, Cancel buttons


## [1.0.1] - 2024-10-28 The Games Afoot

### New Features
- Games Homepage

### Improvements
- Import posts from RDB Spreadsheet
- Import games from RDL Spreadsheet

## [1.0.0] - 2024-10-18 The Root Database

### New Features
- Django prototype website
- Search bar

## [0.1.0] - 2024-10-12 Flask App Prototype

### New Features
- Flask prototype website
- List of Posts

## [0.0.1] - 2024-9-18 Google Sheets Prototype

### New Features
- Tabs for Faction, Vagabonds, Maps, Decks, Hirelings and Landmarks
- Filter by Designer, Name, Reach, Type