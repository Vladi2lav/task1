from django.contrib import admin

from .models import Broker, Exchange, ParseRun, Participant, Product, Trade

admin.site.register([Exchange, Product, Participant, Broker])


@admin.register(ParseRun)
class ParseRunAdmin(admin.ModelAdmin):
    list_display = ('exchange', 'started_at', 'status', 'rows_added', 'rows_skipped', 'trigger')
    list_filter = ('exchange', 'status')


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ('trade_date', 'exchange', 'product', 'buyer', 'seller', 'amount')
    list_filter = ('exchange',)
    search_fields = ('product__name', 'buyer__name', 'seller__name')
    raw_id_fields = ('product', 'buyer', 'seller', 'buyer_broker', 'seller_broker', 'parse_run')
