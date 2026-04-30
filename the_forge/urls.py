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
    path('forge/sheet/<int:pk>/pdf/', views.factionsheet_pdf, name='forge-sheet-pdf'),
    path('forge/sheet/<int:pk>/preview/', views.factionsheet_preview, name='forge-sheet-preview'),
    path('forge/sheet/<int:pk>/preview/save/', views.factionsheet_preview_save, name='forge-sheet-preview-save'),
    path('forge/sheet/<int:pk>/delete/', views.factionsheet_delete, name='forge-sheet-delete'),
    path('hx/forge/sheet/<int:pk>/flavor/edit/', views.sheet_flavor_edit, name='forge-sheet-flavor-edit'),
    path('hx/forge/sheet/<int:pk>/crafted/toggle/', views.sheet_crafted_toggle, name='forge-sheet-crafted-toggle'),
    path('hx/forge/sheet/<int:pk>/layout/toggle/', views.sheet_layout_toggle, name='forge-sheet-layout-toggle'),
    path('hx/forge/sheet/<int:pk>/decree/toggle/', views.sheet_decree_toggle, name='forge-sheet-decree-toggle'),

    # FactionBack
    path('forge/<int:faction_pk>/back/new/', views.factionback_create, name='forge-back-create'),
    path('forge/back/<int:pk>/', views.factionback_edit, name='forge-back-edit'),
    path('forge/back/<int:pk>/pdf/', views.factionback_pdf, name='forge-back-pdf'),

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
    path('forge/setup-card/<int:pk>/pdf/', views.setup_card_pdf, name='forge-setup-card-pdf'),
    path('hx/forge/setup-card/<int:card_pk>/setup-step/add/', views.setup_card_step_add, name='forge-setup-card-step-add'),
    path('hx/forge/setup-card/<int:card_pk>/setup-step/reorder/', views.setup_card_step_reorder, name='forge-setup-card-step-reorder'),

    # FactionAbility (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/ability/add/', views.ability_add, name='forge-ability-add'),
    path('hx/forge/ability/<int:pk>/edit/', views.ability_edit, name='forge-ability-edit'),
    path('hx/forge/ability/<int:pk>/delete/', views.ability_delete, name='forge-ability-delete'),
    path('hx/forge/sheet/<int:sheet_pk>/ability/reorder/', views.ability_reorder, name='forge-ability-reorder'),

    # ContentBox (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/content-box/add/', views.contentbox_add, name='forge-contentbox-add'),
    path('hx/forge/sheet/<int:sheet_pk>/content-box/reorder/', views.contentbox_reorder, name='forge-contentbox-reorder'),
    path('hx/forge/content-box/<int:pk>/edit/', views.contentbox_edit, name='forge-contentbox-edit'),
    path('hx/forge/content-box/<int:pk>/delete/', views.contentbox_delete, name='forge-contentbox-delete'),
    path('hx/forge/content-box/<int:content_box_pk>/steps/reorder/', views.phasestep_reorder_in_box, name='forge-contentbox-step-reorder'),

    # PhaseStep (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/phase-step/add/', views.phasestep_add, name='forge-phasestep-add'),
    path('hx/forge/phase-step/<int:pk>/edit/', views.phasestep_edit, name='forge-phasestep-edit'),
    path('hx/forge/phase-step/<int:pk>/delete/', views.phasestep_delete, name='forge-phasestep-delete'),
    path('hx/forge/sheet/<int:sheet_pk>/phase-step/<str:phase>/reorder/', views.phasestep_reorder, name='forge-phasestep-reorder'),

    # StepAction (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/action/add/', views.stepaction_add, name='forge-stepaction-add'),
    path('hx/forge/action/<int:pk>/edit/', views.stepaction_edit, name='forge-stepaction-edit'),
    path('hx/forge/action/<int:pk>/delete/', views.stepaction_delete, name='forge-stepaction-delete'),
    path('hx/forge/phase-step/<int:step_pk>/action/reorder/', views.stepaction_reorder, name='forge-stepaction-reorder'),
    path('hx/forge/phase-step/<int:step_pk>/action/form/', views.stepaction_form, name='forge-stepaction-form'),
    path('hx/forge/phase-step/<int:step_pk>/action-type/set/', views.phasestep_action_type_set, name='forge-phasestep-action-type-set'),

    # BorderedBox (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/box/add/', views.borderedbox_add, name='forge-box-add'),
    path('hx/forge/box/<int:pk>/edit/', views.borderedbox_edit, name='forge-box-edit'),
    path('hx/forge/box/<int:pk>/delete/', views.borderedbox_delete, name='forge-box-delete'),

    # Mixed boxes+tracks reorder
    path('hx/forge/phase-step/<int:step_pk>/children/reorder/', views.step_children_reorder, name='forge-step-children-reorder'),

    # CardboardTrack (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/track/add/', views.track_add, name='forge-track-add'),
    path('hx/forge/track/<int:pk>/edit/', views.track_edit, name='forge-track-edit'),
    path('hx/forge/track/<int:pk>/delete/', views.track_delete, name='forge-track-delete'),

    # CardboardSlot (child of CardboardTrack)
    path('hx/forge/track/<int:track_pk>/slot/<int:row>/<int:col>/upsert/', views.slot_upsert, name='forge-slot-upsert'),
    path('hx/forge/slot/<int:pk>/delete/', views.slot_delete, name='forge-slot-delete'),

    # Legend (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/legend/add/', views.legend_add, name='forge-legend-add'),
    path('hx/forge/legend/<int:pk>/edit/', views.legend_edit, name='forge-legend-edit'),
    path('hx/forge/legend/<int:pk>/delete/', views.legend_delete, name='forge-legend-delete'),
    path('hx/forge/legend/<int:legend_pk>/row/add/', views.legend_row_add, name='forge-legend-row-add'),
    path('hx/forge/legend-row/<int:pk>/edit/', views.legend_row_edit, name='forge-legend-row-edit'),
    path('hx/forge/legend-row/<int:pk>/delete/', views.legend_row_delete, name='forge-legend-row-delete'),

    # Scale (child of PhaseStep)
    path('hx/forge/phase-step/<int:step_pk>/scale/add/', views.scale_add, name='forge-scale-add'),
    path('hx/forge/scale/<int:pk>/edit/', views.scale_edit, name='forge-scale-edit'),
    path('hx/forge/scale/<int:pk>/delete/', views.scale_delete, name='forge-scale-delete'),
    path('hx/forge/scale/<int:pk>/save/', views.scale_save, name='forge-scale-save'),

    # DecreeSection + CardSlot (children of FactionSheet / DecreeSection)
    path('hx/forge/decree/<int:pk>/edit/', views.decree_edit, name='forge-decree-edit'),
    path('hx/forge/decree/<int:decree_pk>/card-slot/add/', views.cardslot_add, name='forge-cardslot-add'),
    path('hx/forge/decree/<int:decree_pk>/card-slot/reorder/', views.cardslot_reorder, name='forge-cardslot-reorder'),
    path('hx/forge/card-slot/<int:pk>/edit/', views.cardslot_edit, name='forge-cardslot-edit'),
    path('hx/forge/card-slot/<int:pk>/delete/', views.cardslot_delete, name='forge-cardslot-delete'),

    # CardPile (child of FactionSheet)
    path('hx/forge/sheet/<int:sheet_pk>/card-pile/add/', views.cardpile_add, name='forge-cardpile-add'),
    path('hx/forge/sheet/<int:sheet_pk>/card-pile/reorder/', views.cardpile_reorder, name='forge-cardpile-reorder'),
    path('hx/forge/card-pile/<int:pk>/edit/', views.cardpile_edit, name='forge-cardpile-edit'),
    path('hx/forge/card-pile/<int:pk>/delete/', views.cardpile_delete, name='forge-cardpile-delete'),
]
