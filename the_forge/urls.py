from django.urls import path

from . import views

urlpatterns = [
    # ForgedFaction
    path('forge/', views.forgedfaction_list, name='forge-faction-list'),
    path('forge/new/', views.forgedfaction_create, name='forge-faction-create'),
    path('forge/<int:pk>/', views.forgedfaction_detail, name='forge-faction-detail'),
    path('forge/<int:pk>/edit/', views.forgedfaction_edit, name='forge-faction-edit'),
    path('forge/<int:pk>/delete/', views.forgedfaction_delete, name='forge-faction-delete'),
    path('forge/<int:pk>/pdf/', views.forgedfaction_pdf, name='forge-faction-pdf'),

    # FactionSheet Front
    path('forge/<int:faction_pk>/sheet/new/', views.factionsheet_create, name='forge-sheet-create'),
    path('forge/sheet/<int:pk>/', views.factionsheet_edit, name='forge-sheet-edit'),
    path('forge/sheet/<int:pk>/delete/', views.factionsheet_delete, name='forge-sheet-delete'),

    # FactionBack
    path('forge/<int:faction_pk>/back/new/', views.factionback_create, name='forge-back-create'),
    path('forge/back/<int:pk>/', views.factionback_edit, name='forge-back-edit'),

    # Piece (child of FactionBack)
    path('hx/forge/back/<int:back_pk>/piece/add/', views.piece_add, name='forge-piece-add'),
    path('hx/forge/piece/<int:pk>/edit/', views.piece_edit, name='forge-piece-edit'),
    path('hx/forge/piece/<int:pk>/delete/', views.piece_delete, name='forge-piece-delete'),

    # SetupStep (child of FactionBack)
    path('hx/forge/back/<int:back_pk>/setup-step/add/', views.setup_step_add, name='forge-setup-step-add'),
    path('hx/forge/setup-step/<int:pk>/edit/', views.setup_step_edit, name='forge-setup-step-edit'),
    path('hx/forge/setup-step/<int:pk>/delete/', views.setup_step_delete, name='forge-setup-step-delete'),
    path('hx/forge/back/<int:back_pk>/setup-step/reorder/', views.setup_step_reorder, name='forge-setup-step-reorder'),

    # SetupCard (child of ForgedFaction)
    path('forge/<int:faction_pk>/setup-card/new/', views.setup_card_create, name='forge-setup-card-create'),
    path('forge/setup-card/<int:pk>/', views.setup_card_edit, name='forge-setup-card-edit'),
    path('hx/forge/setup-card/<int:card_pk>/setup-step/add/', views.setup_card_step_add, name='forge-setup-card-step-add'),
    path('hx/forge/setup-card/<int:card_pk>/setup-step/reorder/', views.setup_card_step_reorder, name='forge-setup-card-step-reorder'),

    # FactionAbility (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/ability/add/', views.ability_add, name='forge-ability-add'),
    path('hx/forge/ability/<int:pk>/edit/', views.ability_edit, name='forge-ability-edit'),
    path('hx/forge/ability/<int:pk>/delete/', views.ability_delete, name='forge-ability-delete'),
    path('hx/forge/sheet/<int:sheet_pk>/ability/reorder/', views.ability_reorder, name='forge-ability-reorder'),

    # ContentBox (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/content-box/add/', views.contentbox_add, name='forge-contentbox-add'),
    path('hx/forge/content-box/<int:pk>/edit/', views.contentbox_edit, name='forge-contentbox-edit'),
    path('hx/forge/content-box/<int:pk>/delete/', views.contentbox_delete, name='forge-contentbox-delete'),

    # PhaseStep (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/phase-step/add/', views.phasestep_add, name='forge-phasestep-add'),
    path('hx/forge/phase-step/<int:pk>/edit/', views.phasestep_edit, name='forge-phasestep-edit'),
    path('hx/forge/phase-step/<int:pk>/delete/', views.phasestep_delete, name='forge-phasestep-delete'),

    # StepAction (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/action/add/', views.stepaction_add, name='forge-stepaction-add'),
    path('hx/forge/action/<int:pk>/edit/', views.stepaction_edit, name='forge-stepaction-edit'),
    path('hx/forge/action/<int:pk>/delete/', views.stepaction_delete, name='forge-stepaction-delete'),

    # BorderedBox (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/box/add/', views.borderedbox_add, name='forge-box-add'),
    path('hx/forge/box/<int:pk>/edit/', views.borderedbox_edit, name='forge-box-edit'),
    path('hx/forge/box/<int:pk>/delete/', views.borderedbox_delete, name='forge-box-delete'),

    # CardboardTrack (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/track/add/', views.track_add, name='forge-track-add'),
    path('forge/track/<int:pk>/', views.track_edit, name='forge-track-edit'),
    path('hx/forge/track/<int:pk>/delete/', views.track_delete, name='forge-track-delete'),

    # CardboardSlot (child of CardboardTrack)
    path('hx/forge/track/<int:track_pk>/slot/add/', views.slot_add, name='forge-slot-add'),
    path('hx/forge/slot/<int:pk>/edit/', views.slot_edit, name='forge-slot-edit'),
    path('hx/forge/slot/<int:pk>/delete/', views.slot_delete, name='forge-slot-delete'),

    # DecreeSection + CardSlot (children of FactionSheet / DecreeSection)
    path('hx/forge/sheet/<int:sheet_pk>/decree/add/', views.decree_add, name='forge-decree-add'),
    path('hx/forge/decree/<int:pk>/edit/', views.decree_edit, name='forge-decree-edit'),
    path('hx/forge/decree/<int:pk>/delete/', views.decree_delete, name='forge-decree-delete'),
    path('hx/forge/decree/<int:decree_pk>/card-slot/add/', views.cardslot_add, name='forge-cardslot-add'),
    path('hx/forge/card-slot/<int:pk>/edit/', views.cardslot_edit, name='forge-cardslot-edit'),
    path('hx/forge/card-slot/<int:pk>/delete/', views.cardslot_delete, name='forge-cardslot-delete'),

    # CardPile (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/card-pile/add/', views.cardpile_add, name='forge-cardpile-add'),
    path('hx/forge/card-pile/<int:pk>/edit/', views.cardpile_edit, name='forge-cardpile-edit'),
    path('hx/forge/card-pile/<int:pk>/delete/', views.cardpile_delete, name='forge-cardpile-delete'),
]
