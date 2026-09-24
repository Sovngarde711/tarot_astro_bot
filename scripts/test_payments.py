# -*- coding: utf-8 -*-
"""Оплата звёздами Telegram: весь путь на подставном Telegram.

Запуск: python scripts/test_payments.py

Настоящих денег здесь нет: вместо обращений к Telegram подставляется
запись вызовов, и видно, что именно бот отправил бы. Проверяется то, что
стоит денег, если сломается:

1. <b>Двойное начисление.</b> Telegram повторяет подтверждение оплаты,
   пока бот его не примет. Повтор не должен давать разборы второй раз.
2. <b>Чужой счёт.</b> На незнакомый счёт бот обязан ответить отказом до
   того, как звёзды списаны.
3. <b>Недоплата.</b> Начисляем по факту оплаты, а не по тому, что было
   написано в счёте.
4. <b>Возврат.</b> Умеет только владелец: иначе разбор можно получить и
   тут же забрать деньги обратно.
5. <b>Приём обновлений.</b> Без pre_checkout_query в allowed_updates
   платежи не проходят вовсе, а подтверждение приходит сообщением без
   текста — его легко потерять.
"""
import io
import os
import sys
import tempfile

PROJECT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(PROJECT, "bot"))
sys.path.insert(0, os.path.join(PROJECT, "data"))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:TESTTOKEN")

work = tempfile.mkdtemp()
os.environ["BILLING_DIR"] = work

import billing
import payments
import bot as B

billing.STORE_DIR = work
billing.LEDGER_PATH = os.path.join(work, "usage.json")

FAILS = []


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  БЕДА ") + name + (" — " + detail if detail else ""))
    if not ok:
        FAILS.append(name)


CALLS = []
ANSWER = {"ok": True, "result": {}}


def fake_api(method, **params):
    CALLS.append((method, params))
    return ANSWER


B.api_call = fake_api
B.send_message = lambda chat_id, text, markup=None: CALLS.append(
    ("sendMessage", {"chat_id": chat_id, "text": text}))


def last(method):
    for name, params in reversed(CALLS):
        if name == method:
            return params
    return None


print("=== 1. Цены и скидки ===")
for count, stars, discount in payments.packs():
    full = payments.PRICE * count
    real = 0 if not full else round((full - stars) * 100.0 / full, 1)
    print("  %2d разб. — %3d ⭐ (обещано −%d%%, на деле −%s%%)"
          % (count, stars, discount, real))
    check("скидка на %d не меньше обещанной" % count, real >= discount,
          "обещано %d%%, дано %s%%" % (discount, real))

check("один разбор стоит 50", payments.stars_for(1) == 50)
check("десятка дешевле десяти поштучных",
      payments.stars_for(10) < payments.stars_for(1) * 10)
check("несуществующий набор отвергнут", payments.stars_for(7) is None)

print("\n=== 2. Счёт ===")
order = payments.invoice(5)
check("валюта XTR", order["currency"] == "XTR", str(order["currency"]))
check("provider_token отсутствует", "provider_token" not in order,
      "Telegram требует не передавать его для звёзд")
check("сумма совпадает с ценой набора",
      order["prices"][0]["amount"] == payments.stars_for(5))
check("в счёте один пункт", len(order["prices"]) == 1)

# Пока платная модель выключена, разборы бесплатны — брать за них деньги
# значит продать то, что и так раздаётся, а потом возвращать
billing.ENABLED = False
CALLS.clear()
B.send_invoice(555, 5)
check("при выключенной оплате счёт не выставляется",
      last("sendInvoice") is None)
check("человеку объяснили, почему",
      any("бесплатны" in (p.get("text") or "") for _, p in CALLS))

billing.ENABLED = True
CALLS.clear()
B.send_invoice(555, 5)
sent = last("sendInvoice")
check("счёт ушёл в Telegram", sent is not None)
check("счёт адресован верному чату", sent and sent.get("chat_id") == 555)

CALLS.clear()
B.send_invoice(555, 777)  # такого набора нет
check("на выдуманный набор счёт не выставляется", last("sendInvoice") is None)

print("\n=== 3. Проверка перед списанием ===")
CALLS.clear()
B.handle_pre_checkout({"id": "q1", "invoice_payload": "readings:5"})
answer = last("answerPreCheckoutQuery")
check("свой счёт подтверждается", answer and answer.get("ok") is True)

CALLS.clear()
B.handle_pre_checkout({"id": "q2", "invoice_payload": "readings:999"})
answer = last("answerPreCheckoutQuery")
check("чужой счёт отклоняется", answer and answer.get("ok") is False,
      str(answer))
check("отказ объяснён человеку", answer and answer.get("error_message"))

print("\n=== 4. Оплата зачисляется ===")
CALLS.clear()
B.handle_successful_payment(555, {
    "telegram_payment_charge_id": "charge_A",
    "total_amount": payments.stars_for(5),
    "currency": "XTR",
    "invoice_payload": "readings:5",
})
check("начислено пять разборов", billing.state(555)["paid"] == 5,
      "в запасе %d" % billing.state(555)["paid"])
check("человеку сказали спасибо",
      any("Спасибо" in (p.get("text") or "") for _, p in CALLS))

print("\n=== 5. Повтор того же платежа ===")
B.handle_successful_payment(555, {
    "telegram_payment_charge_id": "charge_A",
    "total_amount": payments.stars_for(5),
    "invoice_payload": "readings:5",
})
check("повтор не начисляет второй раз", billing.state(555)["paid"] == 5,
      "в запасе %d" % billing.state(555)["paid"])

print("\n=== 6. Недоплата ===")
B.handle_successful_payment(556, {
    "telegram_payment_charge_id": "charge_B",
    "total_amount": 120,            # хватает на два разбора по 50
    "invoice_payload": "readings:10",
})
check("начислено по факту оплаты, а не по счёту",
      billing.state(556)["paid"] == 2,
      "в запасе %d вместо 10" % billing.state(556)["paid"])

print("\n=== 7. Платёж с непонятным содержимым ===")
CALLS.clear()
B.handle_successful_payment(557, {
    "telegram_payment_charge_id": "charge_C",
    "total_amount": 50,
    "invoice_payload": "чужая строка",
})
check("ничего не начислено", billing.state(557)["paid"] == 0)
check("человека не бросили молча",
      any("Напишите мне" in (p.get("text") or "") for _, p in CALLS))

print("\n=== 8. Оплаченные разборы тратятся ===")
billing.ENABLED = True
billing.FREE_READINGS = 1
before = billing.state(555)["paid"]
kinds = [billing.charge(555) for _ in range(3)]
after = billing.state(555)
check("первый разбор бесплатный, дальше платные",
      kinds[0] == "free" and kinds[1] == "paid" and kinds[2] == "paid",
      str(kinds))
check("из запаса ушло ровно два", after["paid"] == before - 2,
      "было %d, стало %d" % (before, after["paid"]))

# Владелец может временно сделать бота бесплатным. Пока так, оплаченный
# запас трогать нельзя: человек заплатил за то, что сейчас раздаётся даром
billing.ENABLED = False
paid_before = billing.state(555)["paid"]
used_before = billing.state(555)["used"]
kinds = [billing.charge(555) for _ in range(3)]
paid_after = billing.state(555)
check("при выключенной оплате запас не тратится",
      paid_after["paid"] == paid_before,
      "было %d, стало %d" % (paid_before, paid_after["paid"]))
check("расход при этом всё равно считается",
      paid_after["used"] == used_before + 3)
check("разборы помечены бесплатными", all(k == "free" for k in kinds),
      str(kinds))
billing.ENABLED = True

print("\n=== 9. Возврат ===")
CALLS.clear()
B.ADMIN_CHAT_ID = "999"
B.deliver_refund(555, "/refund charge_A")      # не админ
check("клиент не может вернуть себе сам",
      last("refundStarPayment") is None)

CALLS.clear()
B.deliver_refund(999, "/refund charge_A")      # админ
sent = last("refundStarPayment")
check("возврат отправлен в Telegram", sent is not None)
check("возврат адресован плательщику",
      sent and str(sent.get("user_id")) == "555", str(sent))
check("оплата помечена возвращённой",
      any(p.get("refunded") for p in billing.payments(555)))

CALLS.clear()
B.deliver_refund(999, "/refund charge_A")      # повторный возврат
check("дважды не возвращается", last("refundStarPayment") is None)

CALLS.clear()
B.deliver_refund(999, "/refund несуществующий")
check("неизвестный номер не уходит в Telegram",
      last("refundStarPayment") is None)

print("\n=== 10. Приём обновлений ===")
source = io.open(os.path.join(PROJECT, "bot", "bot.py"), encoding="utf-8").read()
check("бот просит у Telegram pre_checkout_query",
      '"pre_checkout_query"' in source)
check("подтверждение оплаты разбирается до проверки на текст",
      source.index('"successful_payment" in message')
      < source.index('if "text" not in message'))

print("\n=== 11. Оферта ===")
terms = B.offer_text()
check("оферта помещается в одно сообщение", len(terms) < 4000,
      "%d символов" % len(terms))
check("цена в оферте совпадает с настоящей",
      ("%d ⭐" % payments.PRICE) in terms)
check("сказано, что это цифровая услуга", "цифровая услуга" in terms)
check("обещан возврат неиспользованного", "Неиспользованные" in terms)
# Проверяем именно то, что увидит человек: пустая настройка дала бы
# пустую строку, которая «находится» в любом тексте
check("указан контакт для обращений", B.contact() in terms,
      B.contact())
privacy_src = io.open(os.path.join(PROJECT, "bot", "privacy.py"),
                      encoding="utf-8").read()
check("личный контакт не зашит в код",
      "stasia" not in source and "stasia" not in privacy_src)
check("сказано, чем разбор не является",
      "не медицинская" in terms and "не юридическая" in terms)
check("сбываемость не обещана", "сбываемость" in terms)
check("сказано про удаление данных", "/reset" in terms)
check("оферта открывается командой",
      '"terms"' in source and "offer_text()" in source)

# Обещания оферты должны совпадать с поведением бота
check("возврат в оферте есть и в коде", "refundStarPayment" in source)
check("оферта не обещает того, чего бот не делает: срок хранения оплат",
      "не имеют срока" in terms and "не сгорают" in terms)

print("\n=== 12. Политика обработки данных ===")
import store  # noqa: E402

parts = B.privacy_messages()
check("политика разбита на сообщения Telegram",
      parts and all(len(p) < 4096 for p in parts),
      "частей %d, длины %s" % (len(parts), [len(p) for p in parts]))

whole = "\n".join(parts)
check("назван закон", "152-ФЗ" in whole)
check("назван оператор и контакт", B.contact() in whole)
check("указано правовое основание", "ст. 6" in whole)
check("названы цели", "Зачем" in whole)
check("срок хранения совпадает с настоящим",
      "%d дней" % store.RETENTION_DAYS in whole,
      "в коде %d" % store.RETENTION_DAYS)
check("число анкет совпадает с настоящим",
      "%d анкет" % store.MAX_PEOPLE in whole,
      "в коде %d" % store.MAX_PEOPLE)
check("сказано про удаление и отзыв согласия",
      "/reset" in whole and "отозвать согласие" in whole)
check("сказано про право узнать свои данные", "/people" in whole)
check("сказано про трансграничную передачу", "трансгранич" in whole)
check("сказано про данные третьих лиц", "других людей" in whole.lower()
      or "Данные других людей" in whole)
check("политика открывается командой", '"privacy"' in source)
check("оферта ссылается на политику", "/privacy" in B.offer_text())

# Политика не должна обещать того, чего нет: бот действительно не пишет
# вопросы и тексты разборов на диск
check("обещание не хранить вопросы совпадает с кодом",
      "тексты разборов на диск не записываются" in whole
      and "вопрос" not in str(store.SAVED_FIELDS))

print("\n=== ИТОГ ===")
print("Не прошло проверок: %d" % len(FAILS))
for item in FAILS:
    print("  -", item)
sys.stdout.flush()
os._exit(1 if FAILS else 0)
