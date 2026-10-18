"""Обезличивание ИП в демо-снимке для Vercel (setup_tools/anonymize.py)."""
import pytest

from exchanges.models import Participant
from setup_tools.anonymize import anonymize_individuals, is_individual, looks_like_iin


@pytest.mark.parametrize('name', [
    'ИП Тайбагорова Б.Е',
    'ип иванов',
    'ИП.Петров',
    'ИП "Ромашка"',
    'ИП«Уголь-Жаксы»',
    'ИП',
    'Индивидуальный предприниматель Сидоров А.А.',
    'КХ Береке',
    'К/Х Нурлан',
    'Крестьянское хозяйство "Асыл"',
    'Крестьянское (фермерское) хозяйство Жер',
    'Фермерское хозяйство Алтын',
    'ФХ Дала',
    'Иванов И.И. ИП',
])
def test_individual_names(name):
    assert is_individual(name)


@pytest.mark.parametrize('name, bin_', [
    ('ТОО "Адалант777"', '031040005167'),
    ('KC Energy Group ТОО', '231240026921'),
    ('АО "Шубарколь Премиум"', '950440000101'),
    ('Продовольственная контрактная корпорация АО НК', '950440000101'),
    ('Ипотечная компания ТОО', '100140011059'),
    ('КХЛ Трейд ТОО', ''),
    ('Хлебоприемное предприятие ТОНКЕРИС', '031040005167'),
    ('ТОО ИП-Сервис', ''),
])
def test_companies_are_kept(name, bin_):
    assert not is_individual(name, bin_)


def test_iin_detection():
    assert looks_like_iin('610125400073')      # дата рождения 25.01.1961, 7-я цифра 4
    assert not looks_like_iin('231240026921')  # БИН: 5-я цифра 4 — тип организации
    assert not looks_like_iin('12345')
    assert not looks_like_iin('')


def test_participant_with_iin_but_neutral_name_is_individual():
    assert is_individual('Алпамыс', '800826400996')


@pytest.mark.django_db
def test_anonymize_individuals_in_db():
    Participant.objects.create(name='ИП Дарбаев Кобентай Каиркенович', bin='610509301220')
    Participant.objects.create(name='ТОО "Астана Мұнай"', bin='031040005167')
    Participant.objects.create(name='Крестьянское хозяйство Асыл', bin='')

    assert anonymize_individuals() == 2

    names = dict(Participant.objects.values_list('name', 'bin'))
    assert names == {'ИП (данные скрыты) №1': '', 'ТОО "Астана Мұнай"': '031040005167', 'ИП (данные скрыты) №2': ''}
    assert not Participant.objects.filter(name__icontains='Дарбаев').exists()
