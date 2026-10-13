from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.страница_торгов, name='trades'),
    path('analytics/', views.страница_аналитики, name='analytics'),
    path('status/', views.страница_состояния, name='status'),
    path('api/trades/', views.api_итоги, name='api_trades'),
    path('api/trades/export.xlsx', views.api_экспорт_xlsx, name='api_trades_export'),
    path('api/analytics/', views.api_аналитика, name='api_analytics'),
    path('api/filters/', views.api_фильтры, name='api_filters'),
    path('api/status/', views.api_состояние, name='api_status'),
    path('api/collect/', views.api_запуск_сбора, name='api_collect'),
]
