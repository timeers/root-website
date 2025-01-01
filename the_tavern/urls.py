from django.urls import path
from the_gatehouse.views import PNPAssetListView, PNPAssetCreateView, PNPAssetUpdateView

urlpatterns = [
    path('assets/', PNPAssetListView.as_view(), name='asset-list'),
    path('assets/create/', PNPAssetCreateView.as_view(), name='asset-create'),
    path('assets/update/<int:id>/', PNPAssetUpdateView.as_view(), name='asset-update'),
]