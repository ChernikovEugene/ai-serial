# Пульт фермы — API для сайта: видео по шотам на MiniMax H3

Документ для разработчика (агента) сайта. Сайт отправляет шоты — референсы-картинки, реплику, описание действия —
в Пульт студийной рендер-фермы. Пульт сам собирает промпт для модели MiniMax H3, раздаёт шоты по видеокартам,
делает видео со звуком и синхроном губ, доводит его до 1080p и сообщает сайту о ходе работы и о готовом ролике.

Всё в этом документе проверено на ферме 16–20.10.2026.

## 0. Коротко

1. Загрузить картинки-референсы: `POST /uploads` → `id` каждой.
2. Отправить шоты одним запросом: `POST /batches` (шот = поля из §3) → `id` каждого шота; ваш `ref` возвращается.
3. Принимать события на свой `webhook_url` (§5) и показывать прогресс (§6):
   `job.started` → `job.progress`… → `job.stage` (черновик готов, идёт доводка) → `job.progress`… → `job.finished`;
   в конце пачки — `batch.finished`.
4. Скачать финальный ролик по `outputs[].url` (с ключом) и сохранить у себя.

Ролик не приходит в ответе на запрос: генерация и доводка занимают десятки минут. Ответ на запрос — это `id`;
готовый файл — по событию `job.finished` (или при опросе `GET /jobs/{id}`).

## 1. Подключение

| Что | Значение |
|---|---|
| Базовый адрес | `FARMHUB_API_URL`. Будет `https://api.<домен>/api/v1`. До публикации — только из сети студии: `http://<адрес Пульта в сети студии>:8800/api/v1`. |
| Ключ | `FARMHUB_API_KEY` = `fh_...`; в каждом запросе заголовок `Authorization: Bearer <ключ>`, включая скачивание файлов |
| Секрет webhook | `FARMHUB_WEBHOOK_SECRET` = `whsec_...`; им Пульт подписывает события (§5) |

Ключ и секрет выдаёт владелец Пульта. Храните их только на сервере сайта, не в браузере и не в репозитории.
Все запросы к Пульту делает сервер сайта, не браузер пользователя. Сайт видит только свои шоты.

## 2. Как Пульт делает шот

- **Шот = одно задание с одним `id`, два этапа** (`stage`):
  1. `generate` — MiniMax H3 делает видео со звуком и синхроном губ по референсам. 736×1280 для 9:16.
     Результат — черновик в `raw_outputs`.
  2. `finish` — доводка: апскейл SeedVR2 до 1080 по короткой стороне (≈1080×1880 для 9:16), звук: речь расширяется
     до 48 кГц, громкость −14 LUFS (стандарт TikTok, Reels, Shorts). Результат — `outputs`: файл `<имя>-1080p.mp4`
     (H.264, AAC 320 кбит/с).
- **`progress` — общий для шота, 0…1:** генерация 0–0,5, доводка 0,5–1. Прогресс считается по шагам модели:
  может прыгать и подолгу стоять на месте (например, около 0,9 в конце доводки) — это нормально.
- **Сколько шотов сразу.** На ферме 11 видеокарт на 6 ПК. MiniMax H3 держит в памяти ПК 39 ГБ, поэтому одновременно
  идут **6 генераций** (одна на ПК). Вторая карта каждого ПК в это время делает доводку других шотов.
  Пачку из 11 шотов Пульт принимает сразу: 6 стартуют, остальные ждут (`position` — место в очереди).
- **Время** (шот 5–6 с, качество `final`): генерация ≈ 11 мин на RTX 4090, ≈ 24 мин на RTX 3090;
  доводка ≈ 21 мин на 4090, ≈ 38 мин на 3090. Первый запуск модели на карте дольше на 3–5 мин.
  Ориентир: 11 шотов с доводкой — 1,5–2 часа. Черновое качество `draft` — 2–8 мин на шот (на 4090 около минуты, если модель уже загружена), без доводки.
- **Приоритет у Blender-рендера студии.** Если рендер забирает карту посреди генерации, шот возвращается в очередь
  (`job.requeued`) и начинается заново на другой карте; прогресс этапа падает до нуля. До 5 попыток, потом `failed`.

## 3. Шот: что сайт присылает

Сайт описывает шот полями, **промпт для модели пишет Пульт** по проверенному рецепту. Готовый промпт виден в ответе
и событиях: `job.params.prompt`.

| Поле | Обязательно | Язык | Что это |
|---|---|---|---|
| `ref` | желательно | — | ваш id шота (до 200 символов), возвращается во всех ответах и событиях |
| `character` | да, или `face` | — | id загрузки: персонаж в нужном образе (одежда, причёска; лучше в похожей обстановке) |
| `face` | да, или `character` | — | id загрузки: лицо персонажа крупно, анфас |
| `location` | нет | — | id загрузки: место действия |
| `location_text` | нет | **англ.** | место словами, если нет картинки `location` |
| `action` | **да** | **англ.** | что происходит в кадре: `she sits at her computer with her headphones and looks into the camera` |
| `line` | нет | **рус.** | реплика, одна, одного персонажа. Пусто — шот без речи |
| `voice` | нет | **англ.** | как говорит: `normal calm voice`, `excited fast voice`, `whisper`. По умолчанию `normal calm voice` |
| `shot_size` | нет | **англ.** | крупность: `medium shot` (по умолчанию), `close-up`, `wide shot` |
| `camera` | нет | **англ.** | камера; по умолчанию `static camera, locked-off, no camera movement` |
| `style` | нет | **англ.** | стиль; по умолчанию `Cinematic vertical TikTok series, realistic, natural lighting, shallow depth of field` |
| `sound` | нет | **англ.** | звук шота без реплики; по умолчанию `quiet room ambience, no music` |
| `aspect` | нет | — | `9:16` (по умолчанию), `16:9`, `1:1`, `3:4`, `4:3` |
| `seconds` | нет | — | 1–10; по умолчанию по длине реплики (слов / 2,5 + 0,8 с, округлённо, от 3 до 10: 8 слов → 4 с), без реплики — 5 |
| `quality` | нет | — | `final` (по умолчанию: 0,9 Мп, полное качество) или `draft` (быстрый черновик, мыльный) |
| `finish` | нет | — | доводка до 1080p; по умолчанию `true` для `final`, `false` для `draft` |
| `seed` | нет | — | число: тот же seed и те же поля → тот же дубль. Пусто — случайный (каждый раз новый дубль) |
| `priority` | нет | — | 0–100, по умолчанию 50; больше — раньше |

### Правила, без которых получается брак

- **Всё, кроме `line`, — по-английски.** Русский текст вне реплики модель читает вслух. Пульт отклонит шот с кириллицей
  в `action`, `voice`, `camera` и других полях (ошибка 422 с названием поля). Если сценарий на сайте пишется
  по-русски, переводите описания на английский перед отправкой.
- **Имена персонажей не пишите** ни в `action`, ни в `line` (если имя не часть самой реплики): модель может их произнести.
  Персонаж задаётся картинками.
- **Одна реплика одного персонажа на шот.** Диалог — это несколько шотов.
- **Длительность — по реплике.** Если шот заметно длиннее фразы, модель повторяет её. Не задавайте `seconds`
  без нужды: Пульт посчитает сам.
- **Картинки-референсы:** без подписей, рамок, скруглённых углов и коллажей (модель переносит их в видео). Лист персонажа
  с несколькими ракурсами разрежьте на отдельные картинки. Лучшая связка: `character` — персонаж в образе,
  `face` — лицо крупно, `location` — место.
- Если место берётся не из `location`, а из фона картинки `character`, дайте `character` на нейтральном фоне.
- `final` для того, что пойдёт в ролик; `draft` — проверить шот быстро (2–8 мин, мыльно, без доводки).

### Что Пульт напишет для модели (пример)

Шот `{"character": …, "face": …, "location": …, "line": "Сегодня первый раз играю в Доту без Олега",
"voice": "normal calm voice", "action": "she sits at her computer with her headphones and looks into the camera"}` →
```
Cinematic vertical TikTok series, realistic, natural lighting, shallow depth of field. Vertical 9:16 video,
4 seconds, one continuous shot. Location: exactly as in <Picture 3>: the same layout, furniture, props, colors,
materials and lighting; do not add, remove or change anything in it. Character: <Picture 1> (the same person as
<Picture 2>). Strictly preserve the character's identity from the reference images: ... Action: medium shot, she
sits at her computer with her headphones and looks into the camera. Camera: static camera, locked-off, no camera
movement. <Picture 1> says in Russian, in a normal calm voice, clearly articulated: "Сегодня первый раз играю в Доту
без Олега". Lip-sync the spoken line. Only this one line is spoken, nothing else, no repetition. The line is said
once, at the start of the shot; after it the character stops talking, closes the mouth and just looks into the
camera until the end. Clean studio-quality voice recording, close microphone, ...
```
Эта реплика из 8 слов звучит ≈ 3 с, поэтому шот 4 с. Проверено распознаванием речи: реплика звучит один раз.
При 5–6 с модель в части дублей повторяла фразу или её конец.

## 4. Пошагово

### 4.1. Загрузить референсы

```
POST /uploads            multipart/form-data, поле file
→ 200 {"id": "8f2c1a…", "filename": "asya_face.png", "size": 412345}
```
png, jpg, webp, до 200 МБ. Одна загрузка используется в любом количестве шотов: персонажа и локацию сцены загрузите
один раз и подставляйте их `id` во все шоты.

### 4.2. Отправить пачку

```
POST /batches
{
  "webhook_url": "https://ваш-сайт/api/farmhub/webhook",
  "shots": [
    {
      "ref": "scene12-shot1",
      "character": "<id>", "face": "<id>", "location": "<id>",
      "line": "Сегодня первый раз играю в Доту без Олега",
      "voice": "normal calm voice",
      "action": "she sits at her computer with her headphones and looks into the camera"
    },
    {
      "ref": "scene12-shot2",
      "character": "<id>", "face": "<id>", "location": "<id>",
      "action": "she puts on her headphones and turns to the monitor",
      "shot_size": "close-up"
    }
  ]
}
→ 200 {"batch": "2545579487de470d",
       "jobs": [{"id": "71c098a6ebd84bba", "ref": "scene12-shot1", "status": "queued", "stage": "generate",
                 "position": 1, "finish": true, "params": {"prompt": "...", "seconds": 4, ...}, ...}, ...]}
```
- До 50 шотов в пачке; в очереди они идут в порядке списка.
- Сначала проверяются **все** шоты. Если хоть один неверный — ответ `422`, например
  `{"detail": "шот 2 (scene12-shot2): action: только по-английски — русский текст вне реплики модель читает вслух"}`,
  и не ставится **ни один**. Покажите текст пользователю.
- Сохраните `batch` и пары `ref → id`.

### 4.3. Принимать события — §5. Показывать прогресс — §6.

### 4.4. Скачать результат

`outputs[].url` — финал, `raw_outputs[].url` — черновик. До публикации Пульта это путь от корня сервера
(`/api/v1/jobs/{id}/files/{name}`), после — полный адрес; поддержите оба варианта (если начинается с `/` — добавьте
схему и хост из `FARMHUB_API_URL`). Скачивайте с заголовком `Authorization` и сохраняйте у себя: пользователям ссылки
Пульта не отдавать (нужен ключ, хранение не вечное).

### 4.5. Опрос — запасной путь

```
GET /jobs/{id}            шот целиком (тот же объект, что в событиях)
GET /batches/{batch}      {"status": "running"|"done", "shots": 11, "finished": 4, "progress": 0.43, "jobs": [...]}
```
Пока пачка не `done`, опрашивайте `GET /batches/{batch}` раз в 1–2 минуты — на случай пропущенных событий.

### 4.6. Отменить и повторить

- `DELETE /jobs/{id}` → `{"canceled": true}`; придёт `job.finished` со `status: "canceled"`. На этапе доводки отменяется
  доводка, черновик остаётся в `raw_outputs`.
- Повтор — отправить шот заново (новый `id`). Другой дубль — без `seed` или с другим `seed`.

## 5. События (webhook)

Пульт шлёт `POST` с JSON на `webhook_url`. Заголовки: `Content-Type: application/json`, `X-FarmHub-Event: <event>`,
`X-FarmHub-Signature: sha256=<hex>`.

| `event` | Когда | Что показать |
|---|---|---|
| `job.started` | карта взяла шот | «Генерация» |
| `job.progress` | прогресс вырос (на 10 %, или на 1 % спустя 30 с) | обновить полосу |
| `job.requeued` | карту забрал рендер студии / ПК уснул / не хватило памяти | «Ожидает карту», прогресс этапа — с нуля |
| `job.stage` | генерация готова, доводка поставлена | «Доводка до 1080p», можно показать черновик из `raw_outputs` |
| `job.finished` | конец: `status` = `done` / `failed` / `canceled` | `done` → скачать `outputs` |
| `batch.finished` | все шоты пачки закончились | итог пачки |

### Тело события

```json
{
  "event": "job.stage",
  "job": {
    "id": "71c098a6ebd84bba",
    "ref": "scene12-shot1",
    "batch": "2545579487de470d",
    "status": "running",
    "stage": "finish",
    "finish": true,
    "progress": 0.5,
    "position": 0,
    "message": "Farm-05 GPU1 (3090)",
    "outputs": [],
    "raw_outputs": [{"name": "h3-r2v_00003_.mp4", "kind": "video", "size": 4421337,
                     "url": "/api/v1/jobs/71c098a6ebd84bba/files/h3-r2v_00003_.mp4"}],
    "error": null,
    "attempts": 1,
    "created": 1792821926.1, "started": 1792821926.5, "updated": 1792822119.9, "finished": null,
    "template": "h3-r2v",
    "params": {"prompt": "...", "seconds": 4, "aspect": "9:16 (Portrait Widescreen)", "megapixels": 0.9, "turbo": false, "...": "..."}
  }
}
```
`batch.finished`: `{"event": "batch.finished", "batch": "2545579487de470d", "jobs": [<шот>, <шот>, …]}`.

| Поле шота | Значение |
|---|---|
| `status` | `queued` (ждёт карту) · `running` (генерация или доводка) · `done` · `failed` · `canceled` |
| `stage` | `generate` · `finish` |
| `progress` | 0…1 по всему шоту |
| `position` | место в очереди; 0 — уже считается |
| `outputs` | финальные файлы; при доводке появляются только при `done` |
| `raw_outputs` | черновик после генерации (с `job.stage`) |
| `error` | при `failed` — причина. При `done` с `error` — доводка не удалась, в `outputs` черновик |
| `updated` | время последнего изменения шота (Unix, секунды) |
| `message` | для людей: какая карта, почему вернулся в очередь |

### Проверка подписи (обязательно)

HMAC-SHA256 от **сырого тела запроса** ключом `FARMHUB_WEBHOOK_SECRET`; тело берите до разбора JSON.

Node.js (Express):
```js
import crypto from "node:crypto";
import express from "express";

app.post("/api/farmhub/webhook", express.raw({ type: "application/json" }), (req, res) => {
  const got = req.get("X-FarmHub-Signature") || "";
  const want = "sha256=" + crypto.createHmac("sha256", process.env.FARMHUB_WEBHOOK_SECRET)
    .update(req.body).digest("hex");
  if (got.length !== want.length || !crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want)))
    return res.sendStatus(401);
  res.sendStatus(200);                      // ответить сразу
  handleFarmEvent(JSON.parse(req.body));    // обработать в фоне
});
```

Python (FastAPI):
```python
import hashlib, hmac, json, os
from fastapi import BackgroundTasks, Request, Response

@app.post("/api/farmhub/webhook")
async def farmhub_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    want = "sha256=" + hmac.new(os.environ["FARMHUB_WEBHOOK_SECRET"].encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(request.headers.get("X-FarmHub-Signature", ""), want):
        return Response(status_code=401)
    background.add_task(handle_farm_event, json.loads(body))
    return Response(status_code=200)
```

### Правила обработки

- **Отвечайте 2xx быстро** (Пульт ждёт до 15 с), скачивание ролика — в фоне.
  На 5xx или таймаут Пульт повторяет: `job.stage`, `job.finished`, `batch.finished` — до 5 раз в течение ~4 минут,
  остальные — один раз через 5 с. На 4xx не повторяет.
- **События одного шота приходят по порядку, но могут повториться.** Храните `updated` последнего применённого события
  шота и пропускайте события с `updated` меньше или равным.
- `webhook_url` должен быть доступен из интернета по https (Пульт ходит на него из студии).

## 6. Как показывать статус на сайте

Храните у шота: `farm_job_id`, `farm_batch_id`, `status`, `stage`, `progress`, `position`, `message`, `draft_url`,
`final_url`, `error`, `updated`. Обновляйте из каждого события (и из опроса `GET /batches/{batch}`).

| Состояние | Условие | Подпись | Полоса |
|---|---|---|---|
| В очереди | `status = queued`, `stage = generate` | «В очереди: №{position}» | 0 % |
| Генерация | `status = running`, `stage = generate` | «Генерация видео — {progress}%» | 0–50 % |
| Ожидает карту | после `job.requeued` | «Карта занята рендером, ждём» | прогресс этапа сброшен |
| Доводка, в очереди | `stage = finish`, `position > 0` | «Черновик готов, ждёт доводки» | 50 % |
| Доводка | `stage = finish`, `position = 0` | «Доводка до 1080p — {progress}%» | 50–100 % |
| Готово | `status = done`, без `error` | «Готово» + плеер `final_url` | 100 % |
| Готово с замечанием | `status = done`, есть `error` | «Готов черновик: {error}» + плеер | 100 % |
| Ошибка | `status = failed` | «Ошибка: {error}» + «Повторить» | — |
| Отменён | `status = canceled` | «Отменён» | — |

- Полосу удобно рисовать из двух частей: «Генерация» (0–50 %) и «Доводка» (50–100 %).
- Процент показывайте как `Math.round(progress * 100)`. Он может стоять на месте по несколько минут — добавьте
  «идёт…» и время с начала этапа, а не ETA по процентам.
- Ориентир по времени, если нужен: генерация ≈ 11 мин (4090) / 24 мин (3090), доводка ≈ 21 / 38 мин.
- После `job.stage` можно сразу дать посмотреть черновик (`raw_outputs`): он уже со звуком, но 720p.
- Для пачки: «Готово {finished} из {shots}» и средний прогресс по шотам (`GET /batches/{batch}` → `progress`).

## 7. Ошибки и ограничения

| Код | Когда |
|---|---|
| 401 | нет ключа или ключ отозван |
| 404 | нет такого шота / пачки / файла (или он чужой) |
| 410 | файл удалён с хранилища |
| 413 | загрузка больше 200 МБ |
| 415 | загружают не картинку / видео / звук |
| 422 | неверный шот: `detail` по-русски, с номером и `ref` шота |

- Пачка — до 50 шотов; на шот — до 3 картинок (`character`, `face`, `location`), одна реплика, один непрерывный
  план до 10 с. Склейку шотов в ролик делает сайт.
- Ферма в студии: ночью ПК могут спать, Пульт будит их сам (до пары минут).

## 8. Что сделать на сайте (чек-лист)

1. Настройки сервера: `FARMHUB_API_URL`, `FARMHUB_API_KEY`, `FARMHUB_WEBHOOK_SECRET`.
2. Форма шота: поля из §3. Описания (`action`, `voice`, `camera`, `shot_size`, `location_text`) — по-английски
   (или автоматический перевод перед отправкой); `line` — по-русски; три слота картинок: персонаж, лицо, место.
   Переключатель «черновик / финал» (`quality`).
3. Кнопка «Сгенерировать»: загрузить новые картинки (`POST /uploads`, уже загруженные не повторять — храните их `id`),
   отправить `POST /batches` со всеми шотами сцены, сохранить `batch` и `ref → id`. При 422 показать `detail`.
4. Обработчик `POST /api/farmhub/webhook`: проверка подписи → ответ 200 → в фоне обновление шота по `ref` / `id`
   с учётом `updated`.
5. Отображение статуса и прогресса по §6; плеер черновика после `job.stage`; плеер финала после `done`.
6. `job.finished` + `done`: скачать `outputs` в своё хранилище, сохранить `final_url`. `failed`: показать `error`
   и кнопку «Повторить» (отправляет шот заново).
7. Страховка: пока пачка не завершена, раз в 1–2 минуты `GET /batches/{batch}`.
8. Кнопка «Отменить» — `DELETE /jobs/{id}`.
