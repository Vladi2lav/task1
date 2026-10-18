from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.trades_page, name='trades'),
    path('analytics/', views.analytics_page, name='analytics'),
    path('news/', views.news_page, name='news'),
    path('status/', views.status_page, name='status'),
    path('legal/terms/', views.legal_terms_page, name='legal_terms'),
    path('legal/privacy/', views.legal_privacy_page, name='legal_privacy'),
    path('legal/cookies/', views.legal_cookies_page, name='legal_cookies'),
    path('legal/sources/', views.legal_sources_page, name='legal_sources'),
    path('api/trades/', views.api_trades, name='api_trades'),
    path('api/trades/export.xlsx', views.api_trades_export, name='api_trades_export'),
    path('api/analytics/', views.api_analytics, name='api_analytics'),
    path('api/filters/', views.api_filters, name='api_filters'),
    path('api/status/', views.api_status, name='api_status'),
    path('api/news/', views.api_news, name='api_news'),
    path('api/collect/', views.api_collect, name='api_collect'),
    path('favicon.ico', views.favicon, name='favicon'),
]
