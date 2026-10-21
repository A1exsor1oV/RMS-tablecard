# Установка на шлюз NC-LCDSRV-NG с Debian/Ubuntu

Цель: открывать RMS-COUVERT с компьютеров в локальной сети по `http://IP-шлюза:8000/`, используя IP с переднего дисплея шлюза. Приложение устанавливается на сам шлюз и запускается службой systemd. Штатный веб-интерфейс продолжает работать на своём порту.

Инструкция рассчитана на доступ к консоли Debian/Ubuntu с правами `sudo`. На конкретном шлюзе установка и автозапуск ещё не проверены. Каталог установки в готовом unit-файле — `/opt/rms-tablecard`, пользователь службы — `rms-tablecard`.

## 1. Проверка шлюза

Выполните в консоли шлюза:

```bash
cat /etc/os-release
uname -m
python3 --version
systemctl --version
sudo ss -ltnp 'sport = :8000'
```

Если последняя команда показывает слушающий процесс, сначала определите, что занимает порт. Для адреса с `:8000` этот порт должен быть доступен нашему сервису. Штатный процесс шлюза останавливать не нужно.

## 2. Файлы и зависимости

Для первой установки скопируйте репозиторий на шлюз в `/opt/rms-tablecard`. Должны существовать файлы `/opt/rms-tablecard/app/main.py` и `/opt/rms-tablecard/deploy/systemd/rms-tablecard.service`. Каталог должен быть доступен для чтения и прохода пользователю службы. Окружение `venv` с Windows не переносится: создайте его на шлюзе.

Если приложение уже было установлено, сохраните действующие `app/config.json` и `app/devices.json` при обновлении исходников.

```bash
sudo apt-get update
sudo apt-get install python3-venv fonts-dejavu-core iputils-ping curl
sudo python3 -m venv /opt/rms-tablecard/venv
sudo /opt/rms-tablecard/venv/bin/python -m pip install -r /opt/rms-tablecard/app/requirements.runtime.txt
```

Версии зависимостей закреплены в `requirements.runtime.txt`. Если установка пакетов завершилась ошибкой, сначала устраните её с учётом версии Python и архитектуры шлюза; к запуску службы переходите после успешной установки.

## 3. Настройки и пользователь службы

При установке приложения на сам шлюз задайте в `app/config.json` адрес местного API управления табличками:

```json
{
  "gateway_host": "127.0.0.1",
  "gateway_port": 5003
}
```

Это адрес API TableCard Gateway, к которому обращается наш сервер. Для работы с табличками подтвердите, что API шлюза действительно доступен по этому адресу. Внешний адрес интерфейса RMS-COUVERT задаётся отдельно: `0.0.0.0:8000` в unit-файле. IP с дисплея в конфигурацию приложения вписывать не требуется.

Создайте служебного пользователя, если его ещё нет, и дайте ему право менять JSON-файлы и записывать в каталог `app/`. Запись в каталог нужна для атомарной замены `devices.json` через временный файл:

```bash
id rms-tablecard >/dev/null 2>&1 || sudo useradd --system --user-group --home-dir /opt/rms-tablecard --no-create-home --shell /usr/sbin/nologin rms-tablecard
sudo chown rms-tablecard:rms-tablecard /opt/rms-tablecard/app/config.json /opt/rms-tablecard/app/devices.json
sudo chown rms-tablecard:rms-tablecard /opt/rms-tablecard/app
sudo chmod 0755 /opt/rms-tablecard/app
sudo chmod 0644 /opt/rms-tablecard/app/config.json /opt/rms-tablecard/app/devices.json
```

`devices.json` автоматически дополняется при запуске и затем после каждого интервала поиска (15 секунд). Добавляются только MAC со статусом шлюза `online`: им присваиваются пустое имя, профиль `nameplate7` и локальный статус `base`; существующие записи сохраняются. Записи `offline` не показываются в списке выбора, даже если их прежний IP уже занят другой табличкой и отвечает на ping. Кнопка «Найти новые таблички» запускает поиск немедленно. Кнопка ⟳ отдельно сбрасывает статусы всех табличек на `base`, возвращая заполненные устройства в список, если они доступны.

Оператор задаёт названия в меню **Таблички**, сверяя MAC-адреса. Шрифты выбираются в блоках ФИО и должности. Установленный выше пакет `fonts-dejavu-core` предоставляет семейства DejaVu. Для дополнительных семейств Liberation можно установить `fonts-liberation2`. Интерфейс предлагает только доступные на сервере поддерживаемые шрифты. После переноса сервиса с Windows на Linux проверьте выбранное семейство в серверном предпросмотре: набор установленных шрифтов может отличаться.

## 4. Автозапуск

```bash
sudo install -m 0644 /opt/rms-tablecard/deploy/systemd/rms-tablecard.service /etc/systemd/system/rms-tablecard.service
sudo systemd-analyze verify /etc/systemd/system/rms-tablecard.service
sudo systemctl daemon-reload
sudo systemctl enable --now rms-tablecard.service
sudo systemctl status rms-tablecard.service --no-pager
```

Служба запускает Uvicorn на всех IPv4-интерфейсах шлюза, порт 8000. Включён запуск при загрузке ОС и перезапуск после сбоя. Используется один рабочий процесс: фоновый поиск и блокировка `devices.json` общие для потоков этого процесса. Не запускайте второй экземпляр сервиса с тем же файлом устройств. При недоступном API шлюза интерфейс продолжает работать, а поиск повторяется автоматически.

## 5. Проверка доступа

На шлюзе:

```bash
curl --fail http://127.0.0.1:8000/health
sudo ss -ltnp 'sport = :8000'
```

Ожидается ответ со `"status":"ok"` и слушающий адрес `0.0.0.0:8000`. `/health` подтверждает работу нашего сервиса, но не связь с табличками.

На другом компьютере в той же сети откройте `http://IP-шлюза:8000/`. Например, если на дисплее `192.168.1.6`, адрес интерфейса — `http://192.168.1.6:8000/`. Адрес `0.0.0.0` в браузере вводить не нужно. После изменения IP шлюза используйте новый IP с тем же портом.

Если на шлюзе сервис доступен, а с другого компьютера нет, проверьте межсетевой экран и связь между устройствами. Если используется активный UFW, разрешите TCP-порт 8000 для локальной подсети. Пример для сети `192.168.1.0/24` (замените её на фактическую):

```bash
sudo ufw status
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp
```

После плановой перезагрузки шлюза повторите проверку страницы с другого компьютера — это подтвердит фактическую работу автозапуска на устройстве.

## Обслуживание

```bash
# Журнал
sudo journalctl -u rms-tablecard.service -n 100 --no-pager

# Перезапуск после обновления кода или ручного изменения config.json
sudo systemctl restart rms-tablecard.service

# Проверка включения автозапуска
systemctl is-enabled rms-tablecard.service

# Остановка и отключение автозапуска нашего сервиса
sudo systemctl disable --now rms-tablecard.service
```

Параметры сетевого запуска описаны в [документации Uvicorn](https://uvicorn.dev/settings/#socket-binding), параметры перезапуска — в [документации systemd](https://www.freedesktop.org/software/systemd/man/257/systemd.service.html#Restart=).
