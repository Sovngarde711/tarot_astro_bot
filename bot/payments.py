# -*- coding: utf-8 -*-
"""Оплата разборов звёздами Telegram.

<b>Почему именно звёзды.</b> Telegram требует, чтобы цифровые товары и
услуги внутри ботов продавались только за звёзды: карты, ЮKassa и прочие
провайдеры разрешены для настоящих вещей и услуг в реальном мире, а
разбор — цифровая услуга. Так что выбора здесь нет, и это к лучшему:
платит человек внутри Telegram, реквизиты карты бот не видит и не хранит.

<b>Что здесь есть.</b> Только знание о ценах и о том, как выглядит счёт и
как проверить оплату. Отправка счёта и ответы Telegram — в bot.py: тогда
всё, что касается денег, можно проверить тестами, не выходя в сеть.

<b>Про возвраты.</b> Telegram позволяет вернуть звёзды методом
refundStarPayment — для этого нужен номер платежа, который приходит
вместе с подтверждением. Этот номер сохраняется в учёте (billing.py),
иначе вернуть деньги будет нечем.
"""
import os

# Цена одного разбора в звёздах. Задаётся переменной окружения, чтобы
# поменять её можно было без правки кода — цену придётся подбирать.
PRICE = int(os.environ.get("STAR_PRICE") or 50)

# Наборы: сколько разборов и сколько звёзд за них. Чем больше набор, тем
# заметнее скидка. Тройке она нужна не меньше прочих: набор, который
# стоит ровно столько же, сколько три отдельные покупки, не берут — он
# ничего не даёт взамен решения заплатить вперёд.
DISCOUNTS = ((1, 0), (3, 3), (5, 5), (10, 10))


def stars_for(count):
    """Сколько звёзд стоит набор из count разборов."""
    for size, discount in DISCOUNTS:
        if size == count:
            full = PRICE * size
            # Саму скидку округляем вверх, в пользу клиента. При целых
            # звёздах пять разборов дают 12,5 звезды скидки: округлив их
            # вниз, мы обещали бы 5%, а давали 4,8% — мелочь, из-за
            # которой обещание перестаёт быть правдой.
            return full - (full * discount + 99) // 100
    return None


def packs():
    """Наборы для показа: (сколько разборов, сколько звёзд, скидка в %)."""
    return [(size, stars_for(size), discount) for size, discount in DISCOUNTS]


def title_for(count):
    if count == 1:
        return "Разбор по теме"
    return "%d разбора" % count if count < 5 else "%d разборов" % count


def invoice(count):
    """Счёт для sendInvoice. None, если такого набора нет.

    provider_token здесь намеренно отсутствует: для звёзд Telegram
    требует не передавать его вовсе — не пустой строкой, а никак.
    """
    stars = stars_for(count)
    if stars is None:
        return None
    if count == 1:
        description = ("Один разбор по любой теме: натальная карта по "
                       "эфемеридам, расклад и разбор под ваш вопрос.")
    else:
        description = ("%d разборов по любым темам. Списываются по одному, "
                       "когда вы их закажете — срок не ограничен."
                       % count)
    return {
        "title": title_for(count),
        "description": description,
        "payload": "readings:%d" % count,
        "currency": "XTR",
        "prices": [{"label": title_for(count), "amount": stars}],
    }


def count_from_payload(payload):
    """Сколько разборов было оплачено. None, если счёт не наш."""
    if not isinstance(payload, str) or not payload.startswith("readings:"):
        return None
    try:
        count = int(payload.split(":", 1)[1])
    except ValueError:
        return None
    return count if stars_for(count) is not None else None


def granted_count(payload, total_amount):
    """Сколько разборов начислить по подтверждённому платежу.

    Считаем и по счёту, и по пришедшей сумме, а начисляем меньшее. Счёт
    составляем мы сами, и Telegram не даёт его подменить, но платёж —
    единственное место, где сходятся наши намерения и чужие деньги;
    сверить два источника здесь дешевле, чем разбираться потом.
    """
    count = count_from_payload(payload)
    if count is None:
        return 0
    expected = stars_for(count)
    try:
        paid = int(total_amount)
    except (TypeError, ValueError):
        return 0
    if paid >= expected:
        return count
    # Заплатили меньше, чем в счёте: начисляем по факту оплаты
    return max(0, paid // PRICE)
