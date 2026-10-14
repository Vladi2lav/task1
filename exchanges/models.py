from django.db import models


class Exchange(models.Model):
    """Биржа-источник данных."""

    code = models.CharField('Код', max_length=20, unique=True)  # ets / caspy
    name = models.CharField('Название', max_length=100)
    website = models.URLField('Сайт', blank=True)

    class Meta:
        verbose_name = 'Биржа'
        verbose_name_plural = 'Биржи'

    def __str__(self):
        return self.name


class Product(models.Model):
    """Товар / биржевой инструмент."""

    name = models.CharField('Наименование', max_length=500)
    code = models.CharField('Код товара', max_length=50, blank=True, db_index=True)
    tnved = models.CharField('Код ТН ВЭД', max_length=30, blank=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        constraints = [
            models.UniqueConstraint(fields=['name', 'code', 'tnved'], name='uniq_product'),
        ]
        indexes = [models.Index(fields=['name'])]

    def __str__(self):
        return self.name


class Participant(models.Model):
    """Покупатель или продавец."""

    name = models.CharField('Наименование', max_length=500)
    bin = models.CharField('БИН', max_length=20, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Участник'
        verbose_name_plural = 'Участники'
        constraints = [
            models.UniqueConstraint(fields=['name', 'bin'], name='uniq_participant'),
        ]
        indexes = [models.Index(fields=['name'])]

    def __str__(self):
        return self.name


class Broker(models.Model):
    name = models.CharField('Наименование', max_length=500, unique=True)

    class Meta:
        verbose_name = 'Брокер'
        verbose_name_plural = 'Брокеры'

    def __str__(self):
        return self.name


class ParseRun(models.Model):
    """Журнал запусков парсера."""

    class Status(models.TextChoices):
        RUNNING = 'running', 'Выполняется'
        SUCCESS = 'success', 'Успешно'
        PARTIAL = 'partial', 'С ошибками'
        FAILED = 'failed', 'Ошибка'

    exchange = models.ForeignKey(Exchange, on_delete=models.CASCADE, related_name='runs', verbose_name='Биржа')
    started_at = models.DateTimeField('Начало', auto_now_add=True)
    finished_at = models.DateTimeField('Окончание', null=True, blank=True)
    status = models.CharField('Статус', max_length=10, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField('Запуск', max_length=20, default='manual')  # manual / schedule
    date_from = models.DateField('Период с', null=True, blank=True)
    date_to = models.DateField('Период по', null=True, blank=True)
    rows_added = models.PositiveIntegerField('Добавлено', default=0)
    rows_skipped = models.PositiveIntegerField('Пропущено (дубли)', default=0)
    error = models.TextField('Ошибки', blank=True)

    class Meta:
        verbose_name = 'Запуск парсера'
        verbose_name_plural = 'Запуски парсера'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.exchange} {self.started_at:%d.%m.%Y %H:%M} — {self.get_status_display()}'


class Trade(models.Model):
    """Одна запись итогов торгов в едином формате."""

    exchange = models.ForeignKey(Exchange, on_delete=models.PROTECT, related_name='trades', verbose_name='Биржа')
    trade_date = models.DateField('Дата торгов', db_index=True)
    trade_time = models.TimeField('Время торгов', null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='trades', verbose_name='Товар')
    buyer = models.ForeignKey(
        Participant, on_delete=models.PROTECT, null=True, blank=True, related_name='purchases', verbose_name='Покупатель'
    )
    seller = models.ForeignKey(
        Participant, on_delete=models.PROTECT, null=True, blank=True, related_name='sales', verbose_name='Продавец'
    )
    buyer_broker = models.ForeignKey(
        Broker, on_delete=models.PROTECT, null=True, blank=True, related_name='buyer_trades', verbose_name='Брокер покупателя'
    )
    seller_broker = models.ForeignKey(
        Broker, on_delete=models.PROTECT, null=True, blank=True, related_name='seller_trades', verbose_name='Брокер продавца'
    )

    deals_count = models.PositiveIntegerField('Число сделок', null=True, blank=True)
    quantity = models.DecimalField('Объём', max_digits=18, decimal_places=3, null=True, blank=True)
    unit = models.CharField('Единица измерения', max_length=30, blank=True)

    price_open = models.DecimalField('Цена открытия', max_digits=18, decimal_places=2, null=True, blank=True)
    price_close = models.DecimalField('Цена закрытия', max_digits=18, decimal_places=2, null=True, blank=True)
    price_min = models.DecimalField('Мин. цена', max_digits=18, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField('Макс. цена', max_digits=18, decimal_places=2, null=True, blank=True)
    price_avg = models.DecimalField('Средневзвешенная цена', max_digits=18, decimal_places=2, null=True, blank=True)
    price_with_vat = models.DecimalField('Цена с НДС', max_digits=18, decimal_places=2, null=True, blank=True)
    price_without_vat = models.DecimalField('Цена без НДС', max_digits=18, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField('Сумма сделки, тг', max_digits=20, decimal_places=2, null=True, blank=True, db_index=True)

    delivery_basis = models.CharField('Базис поставки', max_length=200, blank=True)
    delivery_place = models.CharField('Место поставки', max_length=300, blank=True)
    delivery_term = models.CharField('Срок поставки', max_length=200, blank=True)

    source_url = models.URLField('Источник', max_length=500, blank=True)
    source_key = models.CharField('Отпечаток строки', max_length=64, unique=True)
    parse_run = models.ForeignKey(ParseRun, on_delete=models.SET_NULL, null=True, blank=True, related_name='trades')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Итог торгов'
        verbose_name_plural = 'Итоги торгов'
        ordering = ['-trade_date', '-id']
        indexes = [
            models.Index(fields=['exchange', 'trade_date']),
            models.Index(fields=['delivery_place']),
        ]

    def __str__(self):
        return f'{self.trade_date} {self.exchange} {self.product}'


class News(models.Model):
    """Новость с сайта биржи (дополнительное задание: лента новостей)."""

    exchange = models.ForeignKey(Exchange, on_delete=models.CASCADE, related_name='news', verbose_name='Биржа')
    title = models.CharField('Заголовок', max_length=500)
    published_at = models.DateField('Дата публикации', db_index=True)
    url = models.URLField('Ссылка', max_length=500, unique=True)
    summary = models.TextField('Анонс', blank=True)
    image_url = models.URLField('Картинка', max_length=500, blank=True)
    created_at = models.DateTimeField('Загружено', auto_now_add=True)

    class Meta:
        verbose_name = 'Новость'
        verbose_name_plural = 'Новости'
        ordering = ['-published_at', '-id']

    def __str__(self):
        return self.title
