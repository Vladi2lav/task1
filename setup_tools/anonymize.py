"""Обезличивание индивидуальных предпринимателей и физических лиц для публичного демо (Vercel).

Закон РК «О персональных данных и их защите» (№ 94-V, ст. 12 п. 2) требует хранить базы с персональными
данными граждан РК на территории РК, а серверы Vercel находятся за рубежом. Поэтому в демо-снимке
deploy/vercel_demo.sqlite3 имена ИП/крестьянских хозяйств и их ИИН заменяются. Компании (ТОО, АО и т.п.)
остаются как есть. Полная версия (start.bat, Docker) рассчитана на размещение в РК и данные не меняет.
"""
import re

ANONYMOUS_NAME = 'ИП (данные скрыты) №{number}'

# «ИП Иванов», «ИП.Иванов», «ИП "Ромашка"», «Индивидуальный предприниматель ...», «КХ ...», «К/Х ...»,
# «Крестьянское (фермерское) хозяйство ...», «ФХ ...»; а также «Иванов И.И. ИП» в конце названия
_INDIVIDUAL_RE = re.compile(
    r'^\s*["«]?\s*('
    r'ип(?=$|[\s."«»\'-])'
    r'|индивидуальн\w*\s+предпринимател\w*'
    r'|к\s*/\s*х(?=$|[\s."«»\'-])'
    r'|кх(?=$|[\s."«»\'-])'
    r'|фх(?=$|[\s."«»\'-])'
    r'|крестьянск\w*'
    r'|фермерск\w*\s+хозяйств\w*'
    r')'
    r'|\s(ип|кх)\.?\s*$',
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(r'^\s*["«]?\s*(тоо|ао|оао|зао|ооо|пао|гу|кгу|ргп|гкп|кгп|llp|jsc)(?=$|[\s."«»\'-])', re.IGNORECASE)


def looks_like_iin(number: str) -> bool:
    """ИИН физлица: 12 цифр, первые 6 — дата рождения ГГММДД, 7-я — век и пол (1-6).
    У БИН юрлица 5-я цифра — тип организации (4-6), поэтому месяц «40..60» отсекает БИН."""
    digits = (number or '').strip()
    if not re.fullmatch(r'\d{12}', digits):
        return False
    month, day, century = int(digits[2:4]), int(digits[4:6]), digits[6]
    return 1 <= month <= 12 and 1 <= day <= 31 and century in '123456'


def is_individual(name: str, bin_or_iin: str = '') -> bool:
    """ИП, крестьянское хозяйство или физлицо (по названию или по ИИН вместо БИН). Компании — False."""
    if _COMPANY_RE.match(name or ''):
        return False
    return bool(_INDIVIDUAL_RE.search(name or '')) or looks_like_iin(bin_or_iin)


def anonymize_individuals() -> int:
    """Заменить в БД названия и ИИН индивидуальных предпринимателей. Возвращает число обезличенных участников."""
    from exchanges.models import Participant

    count = 0
    for participant in Participant.objects.order_by('id'):
        if not is_individual(participant.name, participant.bin):
            continue
        count += 1
        participant.name = ANONYMOUS_NAME.format(number=count)
        participant.bin = ''
        participant.save(update_fields=['name', 'bin'])
    return count
