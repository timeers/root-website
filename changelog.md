# Changelog

## [1.12.109] - 2026-8-1 Better Bots
This update fills out the bot functionality and adds more complex features for the bot

### New Features
- Manage Guilds page for Guild Moderators
- LFG Roles can be assigned to guilds
- Manage Guild page for Admin and Guild Mods
- /draft command for building a quick draft

### Improvements
- Guilds can now be assigned Moderators (can add invite, assign mods, add LFG roles)

### Bug Fixes

## [1.12.18] - 2026-7-20 Rootelo Links

### New Features
- dwd/profile/mrmirz-4412 redirects to profile/mrmirz
- rejected posts are no longer deleted and display rejection notice for resubmission guidance
- Option to be notified for new posts and when a post is marked as stable

### Improvements
- profile and post name on detail pages now wraps and dynamically resizes
- removed logged in check when viewing profiles
- added cannonical dwd username field from the RDL API
- added cached winrates by platform
- /stats command shows threshold and defaults to official factions only
- RDL import now uses player api to get discord name instead of guessing

### Bug Fixes
- profile detail page was crashing blank box score chart due to rename
- message for submitted posts should now always link to the pending submittal page
- discarded captain no longer cleared when editing a game
- Leaderboard now reliably uses cached values when blank queries are given
- added command to fix dwd usernames

## [1.12.17] - 2026-7-16 More RTM Feedback

### New Features
- My Scheduled Matches page available from the profile dropdown
- Box Score draft auto saved by browser. Prompted to restore when editing/creating game

### Improvements
- Starting leader and selected VB () block appears on one line if wrapping is required
- Tournament box score requirement only requires box score on final submit
- Deleting a game deletes the associated box scores
- Can now delete games directly from the game edit page
- Increased the max ability bar height in the Forge
- Added game's vod link to the matches page

### Bug Fixes
- Send responses to stage sends only responses not all tournament players
- Bracket tab is labeled Matches when at final child (don't know how else to say it)
- Theme editor should no longer lose changes when saving

## [1.12.16] - 2026-7-09 RTM Feedback

### New Features
- Modals added to record game buttons (Submit, Save, Cancel, Delete)

### Improvements
- Starting leader icon displayed on Game Detail page
- Captain icon replaces captain title for selected and undrafted captains on Game Detail Page (saves space)
- caching series games and player counts instead of annotating
- Added "Loading" above spinner to make the animation more clear
- Renamed scorecards to box scores

## [1.12.15] - 2026-7-08 Leaders

### New Features
- Added leaders to game form

### Improvements
- /stats hides winrate and wins when it doesn't make sense
- /stats uses cached winrates when no parameters are supplied
- Added status icon to featured Posts on the homepage

## [1.12.14] - 2026-7-07 A New Home

### New Features
- New home page

### Improvements
- Slightly improved About page
- Forge limits renders to 3 users at a time
- Games recorded for a Series can be edited by recorder for 1 day after submission
- /stats command gives top 5 factions and top 5 players with at least 2 games

### Bug Fixes
- Matches in Guild access Series can only be recorded by participants and moderators
- /stats command correctly counts games without duplicates

## [1.12.13] - 2026-7-05 Dupe Bug

### Bug Fixes
- submit scheduled game twice duplicated players
- /upcoming uses seated players instead of grouped players
- added 30 min grace period to upcoming results

## [1.12.12] - 2026-7-03 Box Scores

### New Features
- option to require box score on Series game submission
- box score on game detail page
- record game from the schedule page (suggested by MrDrouf)

### Improvements
- Primary leaderboard page uses model winrates instead of calculated winrates for faster loading
- pdf engine scales user submitted images when rendering
- move scorecard backend to json for simplicity
- scorecard detail page improvements

### Bug Fixes
- box scores with blank cells count as the value of the previous turn
- Blank Dominance box scores fill in to match game's ending turn
- submitted games page scroll bug
- static image path errors in dev
- /upcoming bot command populates players correctly

## [1.12.11] - 2026-7-01 Scorecard Grid

### New Features
- New scorecard grid to record turn by turn score in the game form

### Improvements
- Get display name from WW
- AllAuth consent should be a one time approval

### Bug Fixes
- Merging players didn't merge their stage membership
- Get display name from WW on login
- width for extra rounds form
- show assets field error on series create
- If series recording access is GUILD any guild members can be added to game
- Validation failed when recording non registered guild member

## [1.12.10] - 2026-6-30 Small Bugs Need Squashing Too

### Bug Fixes
- fixed spelling of demagogue
- selecting DWD from record match page filters factions etc
- Hide hamburger on mobile when search bar expands
- Finally remove white line from universal search results when no results

## [1.12.9] - 2026-6-29 Game Form Improvements

### New Features
- New options when recording games:
- Brazen Demogogue when Dominance played
- Selected Captains
- Discarded Captain when Knaves selected
- Undrafted Captains when Knaves undrafted
- Page for games played with specific captain
- Next Scheduled Match in header dropdown

### Improvements
- Added spinner when filtering games
- Auto fill video link from scheduled match
- Better header for mobile
- Cleaner record game layout

### Bug Fixes
- When viewing the series bracket clicking on a stage without any rounds brings you to the match page instead of the bracket page

## [1.12.8] - 2026-6-26 RTM Bug Hunting

### New Features
- Scheduled match page accessed from bracket
- Add game to additional Series on game detail page

### Improvements
- merge profile admin command improvements
- /upcoming command no longer requires series
- a matches streaming link now shows on the game detail page if no video link is provided
- display name shown in survey results export
- Survey back button now links to SurveyHome/Series/Stage/Post depending on origin
- non discord login page styling improvements

### Bug Fixes
- Reference to PlayerGroup.VideoPlatformChoices cleaned up
- Faction icons appear on completed games on bracket page 
- auto-enroll registration adds respondents to stage as well as series
- static inline images display bug found by Blaise

## [1.12.7] - 2026-6-24 Bot Improvements

### New Features
- /upcoming bot command for scheduled games
- /help bot command for summary of bots

### Improvements
- simplified and improved /law command
- choose link and/or image for search commands
- video link for Games (Twitch or Youtube)
- renamed Workshop tab to Community Resources and removed add button

## [1.12.6] - 2026-6-22 RTM Optimizations

### New Features
- New bot commands with auto fill
- Law of Root bot command
- Host option to view Series as X (moderator, player, logged out)
- Register for Series button displayed on top right of Series page when available

### Improvements
- added display as dropdown to multiple choice questions
- New game button on series page improved 
- Hide tabs on tournament
- Hide asset list on tournament
- Field for Rules Link added to tournament
- Series record status = registered disables round auto complete
- Static files now refresh cache on change with ManifestStaticFilesStorage
- New factions require BGG post or Discord Thread on submittal
- Added AI generated Art field
- Better links on admin messages
- Added rules link to registration surveys
- Surveys check temporary guild membership when user takes survey
- Option to auto enroll registration responses to Series
- improvements to game detail page

### Bug Fixes
- allow other option populates correctly when loading from template
- Adding a new match reopens a Series Round
- Forge editor no longer returns a 403 when deleting steps after logging back in (CSRF token now read fresh per request)
- Translations for submitted factions no longer show in universal search

## [1.12.5] - 2026-6-18 App Features

### New Features
- Added PWA support - Internet connection still required
- Generate API key from profile for downloading game data
- Schedule matches for a series without grouping players
- Option to download survey responses as csv file
- Discord bot for requested DMs

### Improvements
- Games show as columns on wide screens to save space
- Removed confirmation dialogue when adding unregistered players to stages
- New card in Faction settings to view the faction in the Forge if linked
- Tournament Stages can now enable rounds independent of the Tournament
- Reorganized Tournament settings pages
- Record game access for Moderators/Scheduled/Registered Players for Series

### Bug Fixes
- Expansion image bug where the image could be unintentionally deleted
- Faction header image now includes cache busting on detail page (thanks Tricholome). Cache intentionally does not update on list results for now.
- Small icons for factions now has cache busting
- Bug where games could show up twice in list views
- Hirelings other_side image now has cache busting and respects language
- Series player count limit bug restricting when null
- Can no longer register a player in game of restricted series

## [1.12.4] - 2026-5-26 Forge Sync

### New Features
- Link Forge Faction with your existing Faction
- Submit your Forge Faction to the Database
- Sync your Forge Faction with your linked or submitted Faction
- Unlink your Faction so that you can link it with another

### Bug Fixes
- Paragraph height bug found by Blaise

## [1.12.3] - 2026-5-13 Forging Cards

### New Features
- Added Decks to Factions in the Forge
- Added description text to Legends
- Decks & Cards added to TTS JSON
- Decks & Cards added to PDF PNP

### Improvements
- Faction Markers spawn in TTS
- Single step phases partially indent
- Tweaked ability/lore width allocations
- Improved the curves oddly spaced card action arrows

### Bug Fixes
- Inline icons in ability text are a little smaller

## [1.12.2] - 2026-5-10 More Forge Tuning

### New Features
- Adset card added to TTS json download
- Crafted improvements tile added to TTS json
- Double sided Pieces on Board Back
- Tokens and Buildings added to TTS json
- Added VP and Relationship Markers
- Scale Image on back of Faction Board

### Improvements
- Delete button for Forged Factions
- Stable piece images 
- Replaced crafted items svg with dynamicly created box
- Dynamic min width for attribute bars
- Soft warning if name in Forge conflicts with existing faction
- Pieces snap to matching tracks
- Added French and Spanish
- Added quantity limits to most elements
- Forge How-To Table of contents

### Bug Fixes
- Piece name field is now optional
- Fixed (none) text to be black and standard font
- Adset number and text alignment adjusted
- Lock faction board after 1.5 sec in TTS to work around floating boards
- Fixed crafted improvements snap points
- SVG for 6+ not using faction color
- Track title centered over track instead of container
- Card actions indent fixed so that it doesn't overflow left
- Survey home page responsiveness when logged in
- PDF Engine memory improvements

## [1.12.1] - 2026-5-05 Forge-Tuning

### New Features
- Download Front, Back or Adset Card as PDF layers to easily edit in gimp or other programs
- Added counter option to Cardboard Track
- Included Decree Board for TTS json

### Improvements
- Added optional visibility screen behind card piles for better text legibility
- Added secondary faction color for when primary is not legible against white or tan
- Background color layered behind background image when image is partially translucent
- Color options for boxes and card piles
- Theme text and ability text dynamic sizing improvements
- Added text overlay option for Tracks
- Boxes are more dynamically chosen to reduce stretching
- Rich text editor for Track Row headers

### Bug Fixes
- Padding above single step with small text and icon increased
- Vertically center icons on header rows
- Enforce Card Pile, Custom Icon and Character image limits
- Image previews update after download

## [1.12.0] - 2026-5-01 Faction Forge
The Faction Forge is a new addition to the Root Database. It allows users to build their own faction boards from scratch. Write in your abilities, actions and turn steps to create the board. Fine tune the layout with the layout editor. Then download as an image file, high res PDF or TTS object. Thank you to my beta testers KingLuigiNL, verti and davee_39 who helped me work out some issues and gave great suggestions.

### New Features
- Create Forged Factions in the Forge
- Build Faction Board Front and Back as well as Adset Card
- Layout Editor for Faction Board
- Print to PDF
- Print to WebP Image
- Download as .json TTS File
- How to Page
- Style Guide

## [1.11.2] - 2026-4-08 Theme Admin

### New Features
- Admin theme editor UI

### Bug Fixes
- Fixed bug with no theme background causing missing pattern error

## [1.11.1] - 2026-4-04 Survey and Series Bugs
Of course there are bugs...

### New Features
- Tournament advancement page for moderators to manage players between stages

### Improvements
- Warning before regenerating or clearing Tournament groups
- Deck editor save button is disabled and grey to show when no changes have been made
- Added video and discord thread links to scheduled matches
- Advancement page for Tournament organizers. Advance or Eliminate players between stages.

### Bug Fixes
- Profile and Changelog slug create functions
- Match winners recalculated on game edit
- Auto-add tournament players to open stages or all stages if stages not used
- When creating a new stage the user's provided Round name is used instead of Round 1
- Groups are only availble after group generation
- Fixed game total not displaying correctly on Series overview due to old model structure
- Fixed add deck form saving the wrong inputs if multiple forms present
- URL for Rounds that don't use Stages will direct to the correct page
- RDL bug when creating new round for new season
- Visual bug on post approval page when on mobile
- RDL import was using date_registered insted of date_closed for game date. Pre-registered games would display the wrong day
- Expansion images now save correctly


## [1.11.0] - 2026-3-27 A Series of Surveys & Scorecards
This update adds Surveys to the Workshop! Users can also create their own Series for their game group or host a tournament. Scorecards now have a new more user friendly design and games can be edited or deleted after submission. Thank you to safailla and trippingrannys for their suggestions that went into this update.

### New Features
- Surveys (Multiple Choice, Multiple Selection, Open Ended, Yes/No, Scale, Ranking, Availability, Time, Date)
- Surveys can be used as tournament registration forms
- Public, Private, and Discord Guild locked Surveys
- Survey History to view your responses and past surveys
- Send survey responses to a tournament and create groups of players from the responses based on availability questions
- Rearange player turn order in the record game form
- Edit submitted and finalized games
- Add Discord Guilds to Surveys or Tournaments to require guild membership for participation
- Added the ability to link cards to FAQs as reference

### Improvements
- Changelog page now selects the most recent update or scrolls to the selected update
- Discord Guild invite requests now display average response time.
- Leaderboard filter updates url for easier sharing
- Using Celery for Discord status updates
- Scorecards have a more modern look and improved UI
- Improved Series' Asset and Player managment pages
- Option to add Tournament Moderators
- Cleaned up the 404 and 403 pages
- Removed based on Faction (if one exists) from the Stable Status page
- Improved games queryset loading
- RDL now uses new Tournament > Stage > Round format for API
- RDL game import now uses the Match API Token
- Filter games and leaderboards by date range

### Bug Fixes
- Clicking on a link to a Law now respects the user's Reduce Motion preferences
- It is now possible to delete turns from an existing Scorecard
- Fixed bug with RDL player names importing incorrectly
- Fixed modal bug on Guild invite approval form
- RDL cleanup bug that could delete all RDL games fixed
- Fixed bug where new card could be added multiple times by clicking save button repeatedly


## [1.10.2] - 2026-1-14 Battlefield Unification
The Battlefield now has a new tab structure to quickly navigate between games, leaderboards, series and your submitted games. A similar tab structure is added to the Workshop.

### New Features
- Tab view for the Battlefield
- Tab view for the Workshop
- Submitted games page for logged in users

### Improvements
- Universal game filter form used across the site
- Updated feedback form styling
- Added copy link button to card images
- More modern onboarding pages

### Bug Fixes
- Fixed meta issues for Status and Component Games page
- Update button showing for games with no recorder (imported games)
- Leaderboard table overflow on small screens

## [1.10.1] - 2026-1-13 Admin & Notifications
This update brings some bug fixes and admin improvements. Admins now have a page to approve Posts and a page to approve Discord Server requests. These admin actions will send a dismissable notification to the user.

### New Features
- Admin pending posts widget and management system
- UserNotification system for persistent user alerts
- Post approval/rejection workflow with user notifications
- Guild invite approval/rejection notifications with moderator messages
- Pending posts review page with modal-based interface
- Register New Designer - Add new designers directly from the Post form when searching in Designer, Co-Designer, or Artist fields

### Improvements
- Auto-dismiss notifications when clicking View link
- Notifications display in alert bars across all pages
- Post approval changes status to Development
- Admin dashboard shows count of pending submitted posts
- Moved from Bootstrap 4 to Bootstrap 5

### Bug Fixes
- Broken Clockwork Factions links
- Knaves Advanced Search Title showing as Vagabond
- Submittal message showing on Stable faction form
- Submitted posts could be edited before being approved
- Submitted posts could not be edited by new users after approval
- JS fixes when unable to display scorecard data

## [1.10.0] - 2026-1-9 Happy New Year!

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