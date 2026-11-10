"""Local Telegram onboarding and fixed-recipient, deduplicated delivery. Stdlib only."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('TELEGRAM_STATE_DIR', str(ROOT / 'private')))


def private_dir():
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)


def save(name, value):
    private_dir()
    path = STATE / name
    tmp = STATE / (name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    path.chmod(0o600)


def read(name, default=None):
    path = STATE / name
    return json.loads(path.read_text()) if path.exists() else default


class DeliveryError(Exception):
    pass


def api(token, method, payload=None):
    request = Request('https://api.telegram.org/bot' + token + '/' + method,
                      data=json.dumps(payload or {}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=25) as response:
            result = json.load(response)
    except HTTPError as error:
        # Never expose request URLs: Telegram embeds credentials in the URL.
        raise DeliveryError('Telegram отклонил запрос (HTTP %s).' % error.code) from None
    except Exception:
        raise DeliveryError('Нет подтверждения ответа Telegram. Проверьте интернет; не повторяйте отправку автоматически.') from None
    if not result.get('ok'):
        raise DeliveryError('Telegram не подтвердил выполнение запроса.')
    return result['result']


def status():
    config = read('connection.json', {})
    ledger = read('delivery.json', {})
    return {'connected': bool(config.get('token') and config.get('chat_id')),
            'bot_username': config.get('bot_username'),
            'test_confirmed': bool(config.get('test_message_id')),
            'sent_event_keys': [k for k, v in ledger.items() if v.get('status') == 'sent'],
            'uncertain_event_keys': [k for k, v in ledger.items() if v.get('status') == 'pending']}


def send_digest(payload):
    text = payload.get('text', '').strip()
    keys = payload.get('event_keys')
    if not text or len(text.encode('utf-16-le')) // 2 > 3900:
        raise DeliveryError('Текст должен содержать от 1 до 3900 символов UTF-16.')
    if not isinstance(keys, list) or not keys or any(not isinstance(k, str) or not 1 <= len(k) <= 240 for k in keys):
        raise DeliveryError('Нужны короткие уникальные ключи событий event_keys.')
    config = read('connection.json', {})
    if not config.get('token') or not config.get('chat_id') or not config.get('test_message_id'):
        raise DeliveryError('Подключение Telegram ещё не завершено.')
    private_dir()
    with open(STATE / 'send.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = read('delivery.json', {})
        duplicates = [k for k in keys if k in ledger]
        if duplicates:
            raise DeliveryError('Событие уже отправлялось или результат отправки неизвестен. Исключите его из нового сообщения.')
        record = {'status': 'pending', 'time': int(time.time()),
                  'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
        for key in keys:
            ledger[key] = dict(record)
        save('delivery.json', ledger)
        # Pending is saved BEFORE the network call. An ambiguous timeout will not duplicate alerts.
        result = api(config['token'], 'sendMessage', {'chat_id': config['chat_id'], 'text': text,
                     'link_preview_options': {'is_disabled': True}})
        for key in keys:
            ledger[key].update(status='sent', message_id=result['message_id'])
        save('delivery.json', ledger)
        return {'sent': True, 'message_id': result['message_id'], 'events': len(keys)}


HTML = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Криптоновости · подключение Telegram</title><style>
body{font:17px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;background:#101827;color:#edf3fa;margin:0;padding:36px 20px}main{max-width:680px;margin:auto}h1{font-size:30px;line-height:1.2}section{background:#1b273a;padding:24px;border-radius:16px;margin:20px 0}a{color:#8bcfff}input{box-sizing:border-box;width:100%;padding:14px;margin:10px 0;border:1px solid #718096;border-radius:8px;font-size:16px;background:#101827;color:white}button{padding:13px 20px;border:0;border-radius:8px;background:#75d9bc;color:#102a24;font-weight:700;font-size:16px;cursor:pointer}button:disabled{opacity:.5}.muted{color:#afbdd0;font-size:15px}#message{white-space:pre-wrap}code{color:#a5e6d3}label{font-weight:600}</style>
<main><p class="muted">Локальное подключение на вашем Mac</p><h1>Криптоновости в Telegram</h1>
<p>Проверка каждый час. Только существенные события, источники и анализ возможного влияния на рынок.</p>
<section><h2>1. Создайте отдельного бота</h2><p>Откройте <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a>, отправьте <code>/newbot</code> и выберите имя. Имя пользователя должно оканчиваться на <code>bot</code>.</p><p class="muted">Токен вставьте ниже. Не отправляйте его в чат с помощником. Используйте нового бота, который ещё не подключён к другим сервисам.</p>
<form id="form"><label for="token">Токен от BotFather</label><input id="token" type="password" autocomplete="off" placeholder="Вставьте токен" required><button id="connect">Подключить бота</button></form></section>
<section id="step2" hidden><h2>2. Разрешите доставку себе</h2><p><a id="botlink" target="_blank" rel="noreferrer">Открыть своего бота в Telegram</a> и нажать <b>Запустить / Start</b>.</p><p>Затем вернитесь сюда. Кнопка ниже отправит вам одно тестовое сообщение.</p><button id="check">Проверить и отправить тест</button></section>
<p id="message" role="status"></p><p class="muted">Токен передаётся только локальному помощнику и официальному API Telegram. После подключения он хранится в закрытом файле на этом Mac. Форма не даёт доступ к вашей личной переписке.</p>
<p class="muted">После успешного теста напишите в исходном чате «Готово»: помощник проверит результат и включит расписание. Пока мониторинг не запущен.</p></main>
<script>
const base=location.pathname;const msg=document.querySelector('#message');
async function post(route,data){const r=await fetch(base+'/'+route,{method:'POST',headers:{'Content-Type':'application/json','X-Setup-Key':'__KEY__'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok)throw Error(j.error||'Ошибка подключения');return j;}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const b=document.querySelector('#connect');b.disabled=true;msg.textContent='Проверяем подключение…';try{const j=await post('connect',{token:document.querySelector('#token').value});document.querySelector('#token').value='';document.querySelector('#botlink').href=j.start_url;document.querySelector('#step2').hidden=false;msg.textContent='Бот найден. Откройте его по ссылке выше и нажмите Start.';}catch(e){msg.textContent=e.message;}finally{b.disabled=false;}};
document.querySelector('#check').onclick=async()=>{const b=document.querySelector('#check');b.disabled=true;msg.textContent='Проверяем разрешение на доставку…';try{const j=await post('check',{});msg.textContent=j.message;if(!j.connected)b.disabled=false;}catch(e){msg.textContent=e.message;b.disabled=false;}};
</script></html>'''


def setup(port):
    if status()['connected']:
        raise DeliveryError('Бот уже подключён. Для повторной настройки сначала согласуйте замену подключения.')
    setup_key = secrets.token_urlsafe(24)
    page = '/setup/' + setup_key
    pending = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def valid_host(self):
            return self.headers.get('Host') in ('127.0.0.1:%d' % self.server.server_port, 'localhost:%d' % self.server.server_port)

        def reply(self, code, data, kind='application/json'):
            raw = (json.dumps(data, ensure_ascii=False) if kind == 'application/json' else data).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.valid_host() or self.path != page:
                return self.reply(404, {'error': 'Страница не найдена.'})
            self.reply(200, HTML.replace('__KEY__', setup_key), 'text/html')

        def do_POST(self):
            if not self.valid_host() or self.headers.get('X-Setup-Key') != setup_key or self.path not in (page + '/connect', page + '/check'):
                return self.reply(403, {'error': 'Недоступно.'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers['Host']:
                return self.reply(403, {'error': 'Недоступно.'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 1 <= size <= 2048:
                    raise DeliveryError('Некорректный запрос.')
                data = json.loads(self.rfile.read(size))
                if status()['connected']:
                    if not status()['test_confirmed']:
                        raise DeliveryError('Подключение сохранено, но результат тестовой отправки неизвестен. Сообщите помощнику, получили ли вы тест в Telegram; повторная отправка автоматически не выполняется.')
                    return self.reply(200, {'connected': True, 'message': 'Подключение уже сохранено. Напишите помощнику «Готово».'})
                if self.path.endswith('/connect'):
                    token = data.get('token', '').strip()
                    if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,150}', token):
                        raise DeliveryError('Проверьте токен: вставьте только строку от BotFather, без пробелов и пояснений.')
                    bot = api(token, 'getMe')
                    if not bot.get('is_bot') or not bot.get('username'):
                        raise DeliveryError('Не удалось подтвердить бота.')
                    if api(token, 'getWebhookInfo').get('url'):
                        raise DeliveryError('Этот бот уже подключён к другому сервису. Создайте отдельного нового бота.')
                    pending.clear()
                    pending.update(token=token, username=bot['username'], nonce=secrets.token_urlsafe(24), started=int(time.time()), offset=0)
                    return self.reply(200, {'start_url': 'https://t.me/' + bot['username'] + '?start=' + pending['nonce']})
                if not pending:
                    raise DeliveryError('Сначала подключите токен бота.')
                updates = api(pending['token'], 'getUpdates', {'offset': pending['offset'], 'limit': 100, 'timeout': 0, 'allowed_updates': ['message']})
                matches = []
                for update in updates:
                    pending['offset'] = max(pending['offset'], update['update_id'] + 1)
                    m = update.get('message', {})
                    chat = m.get('chat', {})
                    if (m.get('text') == '/start ' + pending['nonce'] and chat.get('type') == 'private'
                        and chat.get('id') == m.get('from', {}).get('id') and m.get('date', 0) >= pending['started']):
                        matches.append(chat['id'])
                if len(set(matches)) > 1:
                    raise DeliveryError('Ссылка запущена в нескольких аккаунтах. Подключите токен заново и откройте ссылку только в своём аккаунте.')
                if not matches:
                    return self.reply(200, {'connected': False, 'message': 'Пока не вижу Start по вашей ссылке. Откройте бота ссылкой выше, нажмите Start и повторите проверку.'})
                config = {'token': pending['token'], 'chat_id': matches[0], 'bot_username': pending['username'], 'connected_at': int(time.time())}
                save('connection.json', config)
                # One delivery probe only. A timeout is not automatically retried.
                result = api(config['token'], 'sendMessage', {'chat_id': config['chat_id'], 'text': 'Подключение криптообзора проверено ✅\nНовости будут приходить сюда. Почасовой мониторинг пока не запущен: вернитесь в исходный чат и напишите «Готово».'})
                config['test_message_id'] = result['message_id']
                save('connection.json', config)
                pending.clear()
                self.reply(200, {'connected': True, 'message': 'Готово: тестовое сообщение отправлено в Telegram. Напишите помощнику «Готово», чтобы включить мониторинг.'})
            except DeliveryError as error:
                self.reply(400, {'error': str(error)})
            except Exception:
                self.reply(400, {'error': 'Не удалось завершить подключение. Токен не выводится в журнал.'})

    with HTTPServer(('127.0.0.1', port), Handler) as server:
        url = 'http://127.0.0.1:%d%s' % (server.server_port, page)
        save('setup-session.json', {'url': url, 'pid': os.getpid()})
        print(url, flush=True)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['setup', 'status', 'send'])
    parser.add_argument('--port', type=int, default=8768)
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            setup(args.port)
        elif args.command == 'status':
            print(json.dumps(status(), ensure_ascii=False))
        else:
            print(json.dumps(send_digest(json.load(sys.stdin)), ensure_ascii=False))
    except DeliveryError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
    except Exception:
        print(json.dumps({'error': 'Локальная операция не завершена; секреты скрыты.'}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
