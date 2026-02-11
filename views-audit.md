# Views Audit Checklist

This document is a checklist for auditing all views on the site for obvious errors. Once a view has been audited, mark it as complete by changing `[ ]` to `[x]`.

**Audit criteria:**
- Check for proper authentication/authorization
- Verify user input validation
- Look for potential security issues (SQL injection, XSS, etc.)
- Ensure proper error handling
- Check for permission checks where needed
- Look for N+1 query issues or inefficient database queries that could cause timeouts
- Check for unbounded loops or expensive operations without pagination
- Verify external API calls have proper timeouts configured

---

## Audit Notes

Record issues found during the audit that need to be fixed or investigated later.

| View | Issue | Priority | Status |
|------|-------|----------|--------|
| component_games | Anonymous users see all games (including unofficial) while authenticated users without `weird` flag only see official games. Logic at line 1477-1480 should also filter for anonymous users. | Medium | Open |
| component_games | `games.count()` on line 1547 runs a full count query on every page load - potential performance issue for large datasets | Low | Open |
| component_games | `len(filtered_games)` on line 1550 loads entire queryset into memory. Should use `paginator.count` instead. | Medium | Open |

---

## django_project/urls.py (Main URLs)

### Password Reset (Django Auth)
- [ ] `password_reset` - PasswordResetView
- [ ] `password_reset_done` - PasswordResetDoneView
- [ ] `password_reset_confirm` - PasswordResetConfirmView
- [ ] `password_reset_complete` - PasswordResetCompleteView

### HTMX/Comments (the_tavern)
- [ ] `game-comment-sent` - game_comment_sent
- [ ] `game-comment-delete` - game_comment_delete
- [ ] `post-comment-sent` - post_comment_sent
- [ ] `post-comment-delete` - post_comment_delete

### User Actions (the_gatehouse)
- [ ] `add-discord-player` - add_player
- [ ] `bookmark-player` - bookmark_player

### Onboarding
- [ ] `onboard-decline` - onboard_decline
- [ ] `onboard-user` - onboard_user

### API
- [ ] `get_options_for_platform` - get_options_for_platform (the_keep)
- [ ] `get_options_for_tournament` - get_options_for_tournament (the_warroom)

### Language
- [ ] `set_language_custom` - set_language_custom

---

## the_gatehouse/urls.py

### Status/Admin
- [ ] `status_check` - status_check
- [ ] `admin-dashboard` - admin_dashboard

### Profile Views
- [ ] `profile` - player_page_view
- [ ] `player-detail` - player_page_view
- [ ] `player-stats` - player_stats
- [ ] `player-creations` - list_view (the_keep)
- [ ] `designer-components` - designer_component_view
- [ ] `artist-components` - artist_component_view
- [ ] `submitted-components` - submitted_component_view
- [ ] `player-games` - player_game_list_view (the_warroom)
- [ ] `post-bookmarks` - post_bookmarks
- [ ] `game-bookmarks` - game_bookmarks

### Settings
- [ ] `user-settings` - user_settings
- [ ] `sync-avatar` - sync_discord_avatar

### Admin User Management
- [ ] `manage-user` - manage_user
- [ ] `players-list` - ProfileListView

### Feedback
- [ ] `post-request` - post_request
- [ ] `general-feedback` - general_feedback
- [ ] `bug-report` - bug_report
- [ ] `post-feedback` - post_feedback
- [ ] `player-feedback` - player_feedback
- [ ] `game-feedback` - game_feedback
- [ ] `law-feedback` - law_feedback
- [ ] `faq-feedback` - faq_feedback
- [ ] `generic-weird-root-invite` - weird_root_invite
- [ ] `weird-root-invite` - weird_root_invite
- [ ] `generic-french-root-invite` - french_root_invite
- [ ] `french-root-invite` - french_root_invite

### Bookmarks
- [ ] `user-bookmarks` - user_bookmarks

### Changelog
- [ ] `changelog-list-view` - latest_changelog_redirect
- [ ] `changelog-select-view` - changelog_select_view

### Guild Management
- [ ] `join-discord-server` - join_discord_server
- [ ] `guild-request` - guild_join_request
- [ ] `guild-invite` - guild_invite_view
- [ ] `mark-guild-invite-clicked` - mark_guild_invite_clicked
- [ ] `pending-guild-invites` - pending_guild_invites
- [ ] `approve-guild-invite` - approve_guild_invite
- [ ] `reject-guild-invite` - reject_guild_invite

### Post Approval
- [ ] `pending-posts` - pending_posts
- [ ] `approve-post` - approve_post
- [ ] `reject-post` - reject_post

### Notifications
- [ ] `dismiss-notification` - dismiss_notification

### Surveys
- [ ] `survey-list` - survey_list_view
- [ ] `survey-create` - survey_create_view
- [ ] `search-posts-for-survey` - search_posts_for_survey
- [ ] `get-tournament-rounds` - get_tournament_rounds
- [ ] `survey-detail` - survey_detail_view
- [ ] `survey-admin` - survey_admin
- [ ] `survey-take` - survey_take_view
- [ ] `survey-edit` - survey_edit_view
- [ ] `survey-preview` - survey_preview_view
- [ ] `survey-duplicate` - survey_duplicate_view
- [ ] `survey-results` - survey_results_view
- [ ] `my-surveys` - my_surveys_view
- [ ] `survey-user-response` - survey_user_response_view
- [ ] `survey-edit-response` - survey_user_response_edit_view

---

## the_keep/urls.py

### Home/About
- [ ] `site-home` - home
- [ ] `keep-about` - about

### Laws
- [ ] `law-home` - law_table_of_contents
- [ ] `manage-law-updates` - manage_law_updates
- [ ] `check-for-law-updates` - check_for_law_updates
- [ ] `law-preview` - law_preview
- [ ] `activate-rule` - activate_rule
- [ ] `archive-rule` - archive_rule
- [ ] `law-of-root` - law_table_of_contents
- [ ] `update-law-of-root` - update_official_laws
- [ ] `post-law-group-create` - create_law_group
- [ ] `copy-first-law` - copy_law_group_view
- [ ] `law-view` - law_group_view
- [ ] `update-law-group` - update_law_group
- [ ] `edit-law-view` - law_group_edit_view
- [ ] `copy-law-group` - copy_law_group_view
- [ ] `delete-law-group` - delete_law_group
- [ ] `compare-law-group` - upload_and_compare_yaml_view
- [ ] `export-laws-yaml` - export_laws_yaml_view
- [ ] `download-rule` - download_rule

### AJAX Law Views
- [ ] `add-law-ajax` - add_law_ajax
- [ ] `move-law-ajax` - move_law_ajax
- [ ] `edit-law-ajax` - edit_law_ajax
- [ ] `edit-law-description-ajax` - edit_law_description_ajax
- [ ] `delete-law-ajax` - delete_law_ajax

### FAQs
- [ ] `faq-home` - faq_home
- [ ] `faq-home-lang` - faq_home
- [ ] `website-faq` - faq_search
- [ ] `lang-faq` - faq_search
- [ ] `faq-add` - FAQCreateView
- [ ] `post-faq-add` - FAQCreateView
- [ ] `faq-view` - faq_search
- [ ] `faq-edit` - FAQUpdateView
- [ ] `faq-delete` - FAQDeleteView

### Archive/Search
- [ ] `archive-home` - list_view
- [ ] `advanced-search` - advanced_search
- [ ] `search` - search_view
- [ ] `universal-search` - universal_search
- [ ] `api-search-posts` - search_posts

### Component Creation
- [ ] `new-components` - new_components
- [ ] `faction-create` - FactionCreateView
- [ ] `clockwork-create` - ClockworkCreateView
- [ ] `map-create` - MapCreateView
- [ ] `deck-create` - DeckCreateView
- [ ] `hireling-create` - HirelingCreateView
- [ ] `landmark-create` - LandmarkCreateView
- [ ] `tweak-create` - TweakCreateView
- [ ] `vagabond-create` - VagabondCreateView
- [ ] `expansion-create` - ExpansionCreateView

### Expansion Views
- [ ] `expansion-detail` - expansion_detail_view
- [ ] `expansion-faq` - faq_home
- [ ] `expansion-law` - expansion_law_group
- [ ] `expansion-update` - ExpansionUpdateView
- [ ] `expansion-delete` - ExpansionDeleteView

### Component Detail Views
- [ ] `map-detail` - ultimate_component_view
- [ ] `deck-detail` - ultimate_component_view
- [ ] `hireling-detail` - ultimate_component_view
- [ ] `landmark-detail` - ultimate_component_view
- [ ] `tweak-detail` - ultimate_component_view
- [ ] `vagabond-detail` - ultimate_component_view
- [ ] `captain-detail` - ultimate_component_view
- [ ] `faction-detail` - ultimate_component_view
- [ ] `clockwork-detail` - ultimate_component_view

### TTS
- [ ] `faction-tts` - download_tts_file

### Deck/Cards
- [ ] `post-cards-router` - post_cards_router
- [ ] `select-deckgroups` - select_deckgroups
- [ ] `add-deckgroup` - add_deckgroup
- [ ] `deckgroup-detail` - view_deckgroup
- [ ] `edit-deckgroup` - edit_deckgroup
- [ ] `delete-card` - delete_card
- [ ] `edit-card` - edit_card
- [ ] `add-card` - add_card
- [ ] `reorder-cards` - reorder_cards

### Component Games
- [x] `map-games` - component_games
- [x] `deck-games` - component_games
- [x] `hireling-games` - component_games
- [x] `landmark-games` - component_games
- [x] `tweak-games` - component_games
- [x] `vagabond-games` - component_games
- [x] `faction-games` - component_games
- [x] `clockwork-games` - component_games

### Component Updates
- [ ] `map-update` - MapUpdateView
- [ ] `deck-update` - DeckUpdateView
- [ ] `hireling-update` - HirelingUpdateView
- [ ] `landmark-update` - LandmarkUpdateView
- [ ] `tweak-update` - TweakUpdateView
- [ ] `vagabond-update` - VagabondUpdateView
- [ ] `faction-update` - FactionUpdateView
- [ ] `clockwork-update` - ClockworkUpdateView

### Post Actions
- [ ] `bookmark-post` - bookmark_post
- [ ] `confirm-stable` - confirm_stable
- [ ] `confirm-testing` - confirm_testing
- [ ] `color-group` - color_group_view
- [ ] `animal-match` - animal_match_view
- [ ] `status-check` - status_check
- [ ] `post-translations` - translations_view
- [ ] `translation-create` - create_post_translation
- [ ] `translation-update` - create_post_translation
- [ ] `post-delete` - PostDeleteView

### Pieces
- [ ] `add-piece` - add_piece
- [ ] `update-piece` - add_piece
- [ ] `delete-piece` - delete_piece

### PNP Assets (Workshop)
- [ ] `asset-list` - PNPAssetListView
- [ ] `my-resources` - MyPNPAssetListView
- [ ] `asset-new` - PNPAssetCreateView
- [ ] `asset-detail` - PNPAssetDetailView
- [ ] `asset-update` - PNPAssetUpdateView
- [ ] `asset-delete` - PNPAssetDeleteView
- [ ] `asset-player` - PNPAssetListView
- [ ] `pin-asset` - pin_asset

---

## the_warroom/urls.py

### Games
- [ ] `record-game` - manage_game
- [ ] `rdl-game-detail` - game_detail_view
- [ ] `game-delete` - game_delete_view
- [ ] `game-update` - manage_game
- [ ] `game-update-info` - GameUpdateView
- [ ] `game-detail` - game_detail_view

### Battlefield
- [ ] `in-progress` - in_progress_view
- [ ] `my-submitted-games` - my_submitted_games_view
- [ ] `games-home` - game_list_view

### Leaderboard
- [ ] `leaderboard-view` - leaderboard_view

### Scorecards
- [ ] `record-old-scorecard` - scorecard_old_manage_view
- [ ] `detail-scorecard` - scorecard_detail_view
- [ ] `update-old-scorecard` - scorecard_old_manage_view
- [ ] `delete-scorecard` - scorecard_delete_view
- [ ] `assign-scorecard` - scorecard_assign_view
- [ ] `assign-effort` - effort_assign_view
- [ ] `scorecard-home` - scorecard_list_view

### Tournaments/Series
- [ ] `tournament-create` - TournamentCreateView
- [ ] `tournament-detail` - tournament_detail_view
- [ ] `tournament-update` - TournamentUpdateView
- [ ] `tournament-designer-update` - TournamentDesignerUpdateView
- [ ] `tournament-delete` - TournamentDeleteView
- [ ] `tournament-players` - tournament_manage_players
- [ ] `tournament-manage-assets` - tournament_manage_assets
- [ ] `tournaments-home` - tournaments_home

### HTMX Pagination
- [ ] `tournament-players-pagination` - tournament_players_pagination
- [ ] `round-players-pagination` - round_players_pagination
- [ ] `round-games-pagination` - round_games_pagination
- [ ] `add-player-to-effort` - add_player_to_effort

### Rounds
- [ ] `round-create` - round_manage_view
- [ ] `round-detail` - round_detail_view
- [ ] `round-players` - round_manage_players
- [ ] `round-update` - round_manage_view
- [ ] `round-delete` - RoundDeleteView

### Leaderboards
- [ ] `tournament-leaderboard` - round_component_leaderboard_view
- [ ] `round-leaderboard` - round_component_leaderboard_view

### HTMX Game Actions
- [ ] `bookmark-game` - bookmark_game
- [ ] `effort-hx-delete` - effort_hx_delete
- [ ] `game-hx-delete` - game_hx_delete

---

## the_warroom/api/urls.py

### API Views
- [x] `api-scorecard-detail` - ScoreCardDetailView
- [x] `scorecard-game` - GameScorecardView
- [x] `api-scorecard-faction` - FactionAverageTurnScoreView
- [x] `api-scorecard-all` - AverageTurnScoreView
- [x] `api-scorecard-player` - PlayerScorecardView

---

## Summary

| App | Total Views | Audited |
|-----|-------------|---------|
| django_project (main) | 13 | 0 |
| the_gatehouse | 54 | 0 |
| the_keep | 74 | 0 |
| the_warroom | 34 | 0 |
| the_warroom/api | 5 | 0 |
| **Total** | **180** | **0** |
