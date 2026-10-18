"""Хранилище статики для продакшена: сжатие и хеши в именах файлов (WhiteNoise).

Отличие от стандартного CompressedManifestStaticFilesStorage: ссылки `sourceMappingURL` в JS не переписываются.
Сторонние минифицированные библиотеки (например, Chart.js) ссылаются на .map-файлы, которых в проекте нет,
и стандартное хранилище в этом случае прерывает collectstatic ошибкой.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class StaticStorage(CompressedManifestStaticFilesStorage):
    patterns = tuple(
        (extension, patterns)
        for extension, patterns in CompressedManifestStaticFilesStorage.patterns
        if extension != '*.js'
    )
