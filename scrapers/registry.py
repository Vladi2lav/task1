"""Реестр парсеров: код биржи -> класс парсера. Новая биржа добавляется одной строкой."""
from .caspy import CaspyScraper
from .ets import EtsScraper

SCRAPERS = {
    'ets': EtsScraper,
    'caspy': CaspyScraper,
}
