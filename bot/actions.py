# -*- coding: utf-8 -*-

from app_configs import creators_file,configs_folder,universal_files
from utils import Utils
from configs import *
import io, threading, socketio, asyncio, aiohttp

Lock = threading.Lock()


# Define a custom exception
class Cancelled(Exception):
    """Custom exception for specific error handling."""
    pass

class AsyncChatClient:
    def __init__(self, ws_url, auth_token, session, timeout=10):
        self.sio = socketio.AsyncClient(logger=False)
        self.ws_url = ws_url
        self.auth_token = auth_token
        self.session = session
        self.timeout = timeout
        self.result = None
        self.temp_ack = None
        self._timer = None

        # Handlers
        self.sio.on("connect", self._on_connect, namespace="/chat")
        self.sio.on("receive_message", self._on_receive, namespace="/chat")
        self.sio.on("message", self._on_any_message, namespace="/chat")
        self.sio.on("error", self._on_error, namespace="/chat")
        self.sio.on("connect_error", self._on_connect_error, namespace="/chat")
        self.sio.on("disconnect", self._on_disconnect, namespace="/chat")

    async def _on_connect(self):
        Utils.write_log("✅ Connected. Checking authentication...")
        await self.sio.emit("authenticate", "ack", namespace="/chat", callback=self._on_auth)

    async def _on_auth(self, resp):
        Utils.write_log(f"🔑 Auth response: {resp}")
        if self._has_error(resp):
            await self._fail_and_disconnect(resp, reason="Auth error (callback)")
            return

        optimistic_id = str(uuid.uuid4())
        payload = {
            "chat": self.pending_chat,
            "content": self.pending_content,
            "optimisticMessageId": optimistic_id,
        }

        Utils.write_log("📤 Sending message...")
        await self.sio.emit("send_message", payload, namespace="/chat", callback=self._on_send)

    async def _on_send(self, resp):
        Utils.write_log(f"📩 Send ACK: {resp}")
        if self._has_error(resp):
            await self._fail_and_disconnect(resp, reason="Send error")
            return
        self.temp_ack = resp
        self._start_timeout()

    async def _on_receive(self, data):
        Utils.write_log("📨 Final receive_message: waiting for message confirmation")
        self._cancel_timeout()
        if self._has_error(data):
            await self._fail_and_disconnect(data, reason="Receive error")
        else:
            self.result = (True, {"ack": self.temp_ack, "receive": data})
            await self.sio.disconnect()

    async def _on_any_message(self, data):
        Utils.write_log(f"📡 Raw message received: {data}")
        parsed = None
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
            except Exception:
                return
        elif isinstance(data, dict):
            parsed = data

        if parsed and self._has_error(parsed):
            await self._fail_and_disconnect(parsed, reason="Auth error (raw msg)")

    async def _on_error(self, data):
        Utils.write_log(f"⚠️ Socket error: {data}")
        await self._fail_and_disconnect(data, reason="Socket¹¹Socket error")

    async def _on_connect_error(self, data):
        Utils.write_log(f"⚠️ Connect error: {data!r} (type={type(data)})")
        if isinstance(data, Exception):
            Utils.write_log(f"Exception details: {str(data)}")
        await self._fail_and_disconnect(data, reason="Connect error")

    async def _on_disconnect(self):
        Utils.write_log("🔌 Disconnected.")

    async def send_message(self, chat_id, content):
        self.pending_chat = chat_id
        self.pending_content = content
        self.result = None
        self.temp_ack = None

        try:
            await self.sio.connect(
                self.ws_url,
                transports=["websocket"],
                namespaces=["/chat"],
                auth={"authorization": f"Bearer {self.auth_token}"}
            )
            await self.sio.wait()
        except Exception as e:
            Utils.write_log(f"❌ Exception during connect: {repr(e)}")
            return False, str(e)

        return self.result

    def _start_timeout(self):
        self._cancel_timeout()
        loop = asyncio.get_event_loop()
        self._timer = loop.call_later(self.timeout, self._on_timeout)

    def _cancel_timeout(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _on_timeout(self):
        Utils.write_log(f"⏳ Timeout: No receive_message after {self.timeout}s")
        self.result = (False, "timeout")
        asyncio.create_task(self.sio.disconnect())

    async def _fail_and_disconnect(self, resp, reason="Error"):
        Utils.write_log(f"⛔ {reason}, disconnecting...")
        self.result = (False, resp)
        self._cancel_timeout()
        await self.sio.disconnect()

    @staticmethod
    def _has_error(resp):
        if resp is None:
            return True
        if isinstance(resp, dict):
            if resp.get("statusCode") and resp.get("statusCode") != 200:
                return True
            if resp.get("error"):
                return True
        if isinstance(resp, str) and "Unauthorized" in resp:
            return True
        return False

class Creator:
    def __init__(self):
        self.proxies = Utils.load_proxies()
        self.headers = {
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json',
            'origin': 'https://app.maloum.com',
            'priority': 'u=1, i',
            'referer': 'https://app.maloum.com/',
            'sec-ch-ua': '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site'
        }
        self._message_cache = {}  # Cache for recent recipients per creator
        
    def format_proxy(self,proxies):
        return proxies['http']

    def generate_sensor_data(self, type='x-auth-resource'):
        if type == 'x-auth-resource':
            return ''.join(random.choices(string.ascii_letters.upper() + string.digits + string.ascii_letters, k=10))
        elif type == 'dsc_r':
            return ''.join(random.choices(string.ascii_letters.upper() + string.digits + string.ascii_letters, k=8))

    async def update_media_id(self, post_id, creator, creator_id):
        async with aiohttp.ClientSession(headers=creator.get('data', {}).get('headers')) as session:
            session.headers.update({'user-agent': Utils.generate_user_agent('android', 1)})
            proxy = self.format_proxy(random.choice(self.proxies)) if not creator.get('reuse_ip') else creator.get('proxies')
            try:
                async with session.get(
                    f'https://api.maloum.com/posts/{post_id}',
                    proxy=proxy,
                    timeout=20
                ) as response:
                    if not response.ok:
                        raise Exception(f'Error fetching media ID for {post_id}: {await response.text()}')
                    media = (await response.json()).get('media', [])
                    if not media:
                        raise Exception(f'No media ID found for {post_id}')
                    media = media[0]
                    media_id = media.get("uploadId")

                    success, msg = self.update(creator, {'media': media, 'post_id': post_id})
                    if not success:
                        raise Exception(f'Error updating creator {creator_id} with media ID {media_id}: {msg}')
                    return True, f'Successfully saved media ID {media_id} for creator {creator_id}'
            except Exception as e:
                return False, f'Error saving media ID {post_id} for creator {creator_id}: {str(e)}'

    async def scrape_users(self, scraper, admin, creator_id, task_id, count=50, limit=50, offset=0):
        try:
            success, task_status = Utils.check_task_status(task_id)
            if not success:
                raise Exception(task_status)
            if task_status['status'].lower() in ['cancelled', 'canceled']:
                return False, 'Task canceled'
            
            client_msg = {'msg': f'Scraping users by {creator_id}', 'status': 'success', 'type': 'message'}
            success,msg = Utils.update_client(client_msg)

            # Check cache for recent recipients
            if creator_id in self._message_cache:
                recent_recipients = self._message_cache[creator_id]
            else:
                success, messages, total_messages = Utils.get_messages(
                    admin=admin, limit=100, offset=0, constraint='creator_id', keyword=creator_id
                )
                if not success:
                    return False, messages
                recent_recipients = {m.get('recipient_id') for m in messages}
                self._message_cache[creator_id] = recent_recipients

            async with aiohttp.ClientSession(headers=scraper.get('headers')) as session:
                session.cookie_jar.update_cookies(scraper.get('cookies'))
                proxy = scraper.get('proxies', self.format_proxy(random.choice(self.proxies))) if scraper.get('reuse_ip', True) else self.format_proxy(random.choice(self.proxies))

                async with session.get(
                    'https://api.maloum.com/content/discovery',
                    params={'limit': limit, 'next': offset, 'dsc_r': self.generate_sensor_data('dsc_r')},
                    proxy=proxy,
                    timeout=20
                ) as response:
                    if not response.ok:
                        raise Exception(f'Could not get posts: {await response.text()}')
                    posts = (await response.json()).get('data', [])

                valid_users = []
                seen_users = set(recent_recipients)
                lock = asyncio.Lock()

                async def process_post(post):
                    try:
                        success, task_status = Utils.check_task_status(task_id)
                        if not success:raise Exception(task_status)
                        if task_status['status'].lower() in ['cancelled', 'canceled']:
                            return False, 'Task canceled'

                        post_id, comment_count, _next = post.get('_id'), post.get('commentCount'), None
                        if not post_id or comment_count is None:raise Exception('Post ID or Comment count missing from post dict')
                        
                        while comment_count > 0:
                            success, task_status = Utils.check_task_status(task_id)
                            if not success:raise Exception(task_status)
                            if task_status['status'].lower() in ['cancelled', 'canceled']:
                                return False, 'Task canceled'
                            
                            params = {'limit': '50'}
                            if _next is not None: params['next'] = _next
                            
                            async with session.get(
                                f'https://api.maloum.com/posts/{post_id}/comments',
                                params=params,
                                proxy=proxy,
                                timeout=20
                            ) as response:
                                if not response.ok: raise Exception(f'could not get comment for post | {post_id} | {await response.text}')
                                
                                data = await response.json()
                                comments, _next = data.get('data', []), data.get('next')
                                comment_count -= len(comments)

                                for comment in comments:
                                    user = comment.get('user')
                                    if not user: continue
                                    
                                    if (Utils.compare_date(comment.get('createdAt')) and not user.get('isCreator', True)):
                                        
                                        async with lock:
                                            if len(valid_users) >= count:
                                                return True, f'{post.get('commentCount')} processed for post {post_id}'
                                            
                                            if user['_id'] not in seen_users:
                                                seen_users.add(user['_id'])
                                                valid_users.append(user)

                                if not _next:break
                        return True, f'{post.get('commentCount')} processed for post {post_id}'
                    except Exception as error:
                        return False, f'{post.get("_id")} failed to process comments | {error}'

                tasks = [process_post(post) for post in posts]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        Utils.write_log(result[1])
                    elif isinstance(result, tuple) and not result[0]:
                        Utils.write_log(result[1])
                
                if not valid_users:raise Exception('No users found')
                client_msg = {'msg': f'Found {len(valid_users)} users by {creator_id}', 'status': 'success', 'type': 'message'}
                success,msg = Utils.update_client(client_msg)
                return True, valid_users

        except Exception as e:
            return False, f'Error scraping users: {e}'

    async def send_messages(self, admin, task_id, creator, scrapers, config, max_actions):
        try:
            success, task_status = Utils.check_task_status(task_id)
            if not success:
                raise Exception(task_status)
            if task_status['status'].lower() in ['cancelled', 'canceled']:
                return False, 'Task canceled'
            
            message_batch = []

            creator_data = creator.get('data', {})
            if not creator_data:
                raise Exception('No creator data')

            auth_token = creator_data['details']['user']['accessToken']
            creator_name = creator_data['details']['user']['username']
            email = creator_data['details']['user']['email']
            password = creator_data['details']['user']['password']
            creator_id = creator_data['details']['user']['_id']
            creator_internal_id = creator['id']

            caption = config.get('caption', '')
            caption_source = config.get('caption_source', 'creator')
            has_media = config.get('has_media', False)
            media = creator_data.get('media', {})
            media_id = media.get('uploadId')
            is_paid = False if config.get('cost_type', 'free') == 'free' else True
            price = config.get('price', 0)

            if caption_source == 'creator':
                captions_file = os.path.join(configs_folder, creator_internal_id, 'captions.txt')
                if not os.path.isfile(captions_file):
                    raise Exception(f'Captions file does not exist for {creator_name}')
                with open(captions_file, 'r', encoding='utf-8') as f:
                    captions = [line.strip() for line in f.readlines()]
                    if not captions:
                        raise ValueError('Captions cannot be empty')

            success, _creator = await self.login(
                admin, email, password, reuse_ip=creator.get('reuse_ip', True), task_id=task_id
            )
            if not success:
                raise Exception(_creator)
            creator = _creator
            
            if len(scrapers) < 1: raise Exception('Scrapers must not be empty')
            target_scraper = random.choice(scrapers)
            success, scraper = await self.login(
                admin, target_scraper['email'], target_scraper['data']['details']['user']['password'],
                reuse_ip=target_scraper.get('reuse_ip', True), task_id=task_id, category='users'
            )
            if not success:
                raise Exception(scraper)

            async with aiohttp.ClientSession() as session:
                session.headers.update(creator_data.get('headers'))
                session.cookie_jar.update_cookies(creator_data.get('cookies'))
                proxy = creator.get('proxies', self.format_proxy(random.choice(self.proxies))) if creator.get('reuse_ip', True) else self.format_proxy(random.choice(self.proxies))

                offset, limit, users, found_users = 0, 50, [], 0
                while found_users < max_actions:
                    success, new_users = await self.scrape_users(
                        scraper, admin, creator_id, task_id, count=max_actions, limit=limit, offset=offset
                    )
                    if not success:
                        raise Exception(new_users)
                    users.extend(new_users)
                    found_users += len(new_users)
                    offset += limit
                    
                message_tasks = []

                for user in users:
                    # Create new chat
                    async with session.post(
                        'https://api.maloum.com/chats',
                        json={'member2': user.get('_id')},
                        proxy=proxy,
                        timeout=20,
                        ssl=False
                    ) as response:
                        if not response.ok:
                            Utils.write_log(f"--- Failed to create chat for user {user['username']}: {await response.text()} ---")
                            
                            client_msg = {'msg': f'Failed to create chat for user {user['username']}: {await response.text()}', 'status': 'error', 'type': 'message'}
                            success, msg = Utils.update_client(client_msg)

                            continue

                        chat_id = (await response.json()).get('_id')
                        if not chat_id:
                            Utils.write_log(f"--- No chat ID found for user {user.get('_id')} ---")
                            continue

                        # Check for existing messages in the chat
                        async with session.get(
                            f'https://api.maloum.com/chats/{chat_id}/messages',
                            params={'limit': 1},
                            proxy=proxy,
                            timeout=20
                        ) as response:
                            if not response.ok:
                                Utils.write_log(f"--- Failed to check messages for chat {chat_id}: {await response.text()} ---")
                                continue
                            messages = (await response.json()).get('data', [])
                            if len(messages) > 0:
                                client_msg = {'msg': f'Skipping chat with user @{user["username"]} by {creator_name} as it already has messages', 'status': 'success', 'type': 'message'}
                                success,msg = Utils.update_client(client_msg)
                                Utils.write_log(f"--- Skipping chat with user @{user["username"]} by {creator_name} as it already has messages ---")
                                continue

                        # Prepare message
                        caption = random.choice(captions) if caption_source == 'creator' else caption

                        json_data = {'type': 'text', 'text': caption}
                        if has_media and media_id:
                            media_info = {
                                "mediaId": media.get("uploadId"),
                                "type": media.get("type"),
                                "width": media.get("width"),
                                "height": media.get("height")
                            }
                            json_data = {
                                "type": "media" if not (is_paid and price > 0) else "chat_product",
                                "media": [media_info],
                                "text": caption
                            }
                            if is_paid and price > 0:
                                json_data["priceNet"] = price

                        # Introduce random delay to avoid rate-limiting
                        await asyncio.sleep(random.randint(2, 5))

                        # Send message
                        client = AsyncChatClient('wss://api.maloum.com/socket.io', auth_token, session)
                        task = client.send_message(chat_id, json_data)
                        message_tasks.append((task, user, chat_id, caption))  # Store task, user, chat_id, and caption

                # Process message tasks
                results = await asyncio.gather(*(task for task, _, _, _ in message_tasks), return_exceptions=True)

                # Build message batch with per-user data
                for (task, user, chat_id, caption), result in zip(message_tasks, results):
                    if isinstance(result, Exception):
                        raise Exception(f"--- Error sending message to {user['username']}: {result} ---")
                    success, response = result
                    
                    if not success:
                        raise Exception(f"--- Failed to send message to {user['username']}: {response} ---")

                    # Add to message batch
                    message_batch.append({
                        'message_id': chat_id,
                        'admin': admin,
                        'creator_id': creator_internal_id,
                        'creator_name': creator_name,
                        'recipient_id': user['_id'],
                        'recipient_name': user['username'],
                        'has_media': has_media,
                        'link': f'https://app.maloum.com/chat/{chat_id}',
                        'sender_status': 'sent',
                        'caption': caption,
                        'price': price
                    })

                # Batch database updates
                for msg in message_batch:
                    success, db_msg = Utils.add_message(**msg)
                    if not success:raise Exception(f'Error adding message to database for {msg["recipient_name"]} by {creator_name}: {db_msg}')
                    Utils.write_log(f'=== Successfully sent a message to {msg["recipient_name"]} by {creator_name} ===')
                    
                    client_msg = {'msg': f'Successfully sent a message to {msg["recipient_name"]} by {creator_name}', 'status': 'success', 'type': 'message'}
                    success, db_msg = Utils.update_client(client_msg)
                    if not success:Utils.write_log(db_msg)

                if len(message_batch) > 0:
                    return True, f'Successfully sent messages to {len(message_batch)} users by {creator_name}'
                else: return False, f'{creator_name} could not send any messages to users'

        except Exception as e:
            return False, f'Error sending messages to users for {creator.get("id")}: {str(e)}'

    async def login(self, admin, email, password, reuse_ip=True, task_id=None, category='creators'):
        async with aiohttp.ClientSession() as session:
            try:
                success, task_status = Utils.check_task_status(task_id) if task_id else (False, 'No task ID provided')
                if not success:
                    raise Exception(task_status)
                if task_status.get('status', '').lower() in ['cancelled', 'canceled']:
                    return False, 'Task canceled'

                success, user = Utils.check_creator(email, admin)
                if not success:raise Exception(user)

                creator_id = user.get('id',None)
                user_data = user.get('data', {})
                new_user = creator_id is None

                if reuse_ip and 'proxies' in user_data:
                    proxy = user_data['proxies']
                else:proxy = self.format_proxy(random.choice(self.proxies))

                if not new_user:
                    session.headers.update(user_data.get('headers', {}))
                    refresh_token = user_data['details']['user']['refreshToken']
                    del session.headers['authorization']

                    #refresh token
                    async with session.post(
                        'https://srswgacczfgjttwdpuia.supabase.co/auth/v1/token',
                        params={'grant_type': 'refresh_token'},
                        json={'refresh_token': refresh_token},
                        proxy=proxy,
                        timeout=60
                    ) as response:
                        if not response.ok:raise Exception(f'could not refresh access token with refresh token {refresh_token}')
                        login_data = await response.json()
                        token, refresh_token = login_data['access_token'], login_data['refresh_token']

                        session.headers.update({
                            'authorization': f'Bearer {token}'
                        })

                        user_data['details']['user'].update({
                            'accessToken':token,
                            'refreshToken':refresh_token,
                        })
                        user_data.update({
                            'headers':dict(session.headers),
                            'proxies':proxy
                        })

                        user_data['cookies'] = {
                            key: str(value) for key, value in session.cookie_jar.filter_cookies('https://api.maloum.com').items()
                        }

                        success, msg = Utils.update_creator(creator_id, email, user_data)
                        if not success:raise Exception(msg)
                        return True, user_data


                session.headers.update(self.headers)
                session.headers.update({'user-agent': Utils.generate_user_agent('android', 1)})
                
                async with session.post(
                    'https://api.maloum.com/user-management/login',
                    json={'usernameOrEmail': email, 'password': password},
                    proxy=proxy,
                    timeout=20
                ) as response:
                    if response.status == 401:
                        return True, 'Credentials not correct'
                    if not response.ok:
                        user_data['status'] = 'Offline'
                        return False, await response.text()

                    login_data = await response.json()
                    token, refresh_token = login_data['accessToken'], login_data['refreshToken']
                    session.headers.update({
                        'apikey': 'sb_publishable_4zljSqmEuxGuqPttJAK_kg_XzInyyJ9',
                        'authorization': f'Bearer {token}',
                        'x-client-info': 'supabase-js-web/2.50.3',
                        'x-supabase-api-version': '2024-01-01',
                    })

                    async with session.get(
                        'https://srswgacczfgjttwdpuia.supabase.co/auth/v1/user',
                        proxy=proxy,
                        timeout=20
                    ) as response:
                        if not response.ok:
                            raise Exception('Could not get user account creds')
                        login_state = await response.json()

                    async with session.get(
                        'https://api.maloum.com/users/current',
                        proxy=proxy,
                        timeout=20
                    ) as response:
                        if not response.ok:
                            raise Exception('Could not get user current creds')
                        account = await response.json()

                    profile = {}
                    if category == 'creators':
                        async with session.get(
                            f'https://api.maloum.com/users/{account["username"]}/profile',
                            proxy=proxy,
                            timeout=20
                        ) as response:
                            if not response.ok:
                                raise Exception('Could not get user profile details')
                            profile = await response.json()

                    data = {
                        'user': {
                            'last_login': login_state.get('last_sign_in_at'),
                            'status': login_state.get('role'),
                            **account,
                            **profile,
                            **login_data
                        }
                    }

                    user_data['status'] = 'Online' if data['user'].get('status') == 'authenticated' else 'Offline'
                    user_data['details'] = data
                    user_data['details']['user']['password'] = password
                    user_data['headers'] = dict(session.headers)
                    user_data['cookies'] = {
                        key: str(value) for key, value in session.cookie_jar.filter_cookies('https://api.maloum.com').items()
                    }
                    user_data['proxies'] = proxy
                    user_data['reuse_ip'] = reuse_ip

                    if new_user:
                        creator_id = str(uuid.uuid4()).upper()[:8]
                        success, msg = Utils.add_creator(creator_id, email, user_data, admin, category=category, task_id=task_id)
                        os.makedirs(os.path.join(configs_folder, creator_id, 'images'), exist_ok=True)
                        os.makedirs(os.path.join(configs_folder, creator_id, 'videos'), exist_ok=True)
                        with open(os.path.join(configs_folder, creator_id, 'captions.txt'), 'w') as file:
                            file.write("")
                    else:
                        success, msg = Utils.update_creator(creator_id, email, user_data)
                        if not success:
                            raise Exception(msg)

                    user_data['id'] = creator_id
                    return True, user_data

            except Exception as e:
                Utils.write_log(f'Error logging in {e} on {email}')
                return False, f'Error logging in {e} on {email}'
            
    def update(self,user:dict,data:dict):
        try:
            user_email,user_id,user = user['email'],user['id'],user['data']
            for key,value in data.items():
                user[key] = value
            success,msg = Utils.update_creator(user_id,user_email,user)
            if not success:raise Exception(msg)
            return True,user
        except Exception as error:
            return False, error
    

class _MALOUM:
    def __init__(self):
        self.proxies = Utils.load_proxies()
        self.headers = {
            'authority': 'rest.4based.com',
            'accept': 'application/json',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json',
            'origin': 'https://4based.com',
            'referer': 'https://4based.com/',
            'sec-ch-ua': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site'
        }

    async def add_creators(self, admin, task, creators, category):
        task_status, task_msg, completed, fails = 'failed', f'Started logging in creators for {task["id"]}', 0, 0
        try:
            Utils.write_log(f'=== Add {category} started for {task["id"]} ===')
            task_id = task['id']

            async def login_creator(creator):
                success, task_status = Utils.check_task_status(task_id)
                if not success:
                    raise Exception(task_status)
                if task_status['status'].lower() in ['cancelled', 'canceled']:
                    return False, 'Task canceled'
                return await Creator().login(admin, creator['email'], creator['password'], task_id=task_id, category=category)

            tasks = [login_creator(creator) for creator in creators]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for success, result in results:
                if success:
                    completed += 1
                    client_msg = {'msg': f'{completed} {category} added so far on task:{task_id}', 'status': 'success', 'type': 'message'}
                    success, msg = Utils.update_client(client_msg)
                    if not success:
                        Utils.write_log(msg)
                elif result == 'Task canceled':
                    task_status = 'canceled'
                    client_msg = {'msg': f'{result} task:{task_id}', 'status': 'error', 'type': 'message'}
                    success, msg = Utils.update_client(client_msg)
                    if not success:
                        Utils.write_log(msg)
                    break
                else:
                    fails += 1
                    client_msg = {'msg': f'{fails} creators failed so far on task:{task_id}', 'status': 'error', 'type': 'message'}
                    success, msg = Utils.update_client(client_msg)
                    if not success:
                        Utils.write_log(msg)
                    task_msg = result

                Utils.write_log(task_msg)

        except Exception as e:
            Utils.write_log(e)
            task_status = 'failed'
            task_msg = f'Error adding creators on {task_id}: {e}'

        finally:
            if task_status == 'canceled':
                client_msg = {'msg': f'{task_id} was canceled', 'status': 'error', 'type': 'message'}
                task_msg = client_msg['msg']
            elif completed == len(creators) and len(creators) > 0:
                task_status = 'success'
                client_msg = {'msg': f'{task_id} successful', 'status': 'success', 'type': 'message'}
            elif fails > len(creators) // 2:
                client_msg = {'msg': f'{task_id} failed', 'status': 'error', 'type': 'message'}
                task_status = 'failed'
                task_msg = client_msg['msg']
            elif task_status == 'failed':
                client_msg = {'msg': f'{task_id} failed', 'status': 'error', 'type': 'message'}
            else:
                task_status = 'completed'
                client_msg = {'msg': f'{completed} items successful task:{task_id}', 'status': 'success', 'type': 'message'}
                task_msg = client_msg['msg']

            success, msg = Utils.update_client(client_msg)
            if not success:
                Utils.write_log(msg)

            success, msg = Utils.update_task(task_id, {'status': task_status, 'message': task_msg})
            if not success:
                Utils.write_log(msg)

            task_data = task
            task_data.update({'updated': str(datetime.now()), 'status': task_status})
            success, msg = Utils.update_client({'task': task_data, 'type': 'task'})
            if not success:
                Utils.write_log(msg)

    async def start_messaging(self, task, max_actions=20):
        task_status, task_msg = 'failed', f'Started messaging for {task["id"]}'
        try:
            admin = task['admin']
            task_id = task['id']
            config = task['config']
            selected_creators = config.get('select-creators', [])
            time_between = config.get('time_between', 60)
            time_message = {
                '60': '1 minute', '120': '2 minutes', '180': '3 minutes', '300': '5 minutes',
                '600': '10 minutes', '1200': '20 minutes', '1800': '30 minutes', '3600': '1 hour',
                '7200': '2 hours', '10800': '3 hours', '21600': '6 hours', '86400': '24 hours'
            }

            success, creators, total_creators = Utils.get_creators(admin=admin, limit=100, selected_creators=selected_creators)
            if not success:
                raise Exception(creators)

            len_creators = len(creators)
            if len_creators < total_creators:
                for i in range(total_creators - len_creators):
                    offset = len_creators + i
                    success, msg, total_creators = Utils.get_creators(admin=admin, limit=100, offset=offset, selected_creators=selected_creators)
                    if not success:
                        raise Exception(msg)
                    creators.extend(msg)

            success, scrapers, total_scrapers = Utils.get_creators(admin=admin, limit=100, category='users')
            if not success:
                raise Exception(scrapers)

            len_scrapers = len(scrapers)
            if len_scrapers < total_scrapers:
                for i in range(total_scrapers - len_scrapers):
                    offset = len_scrapers + i
                    success, msg, total_scrapers = Utils.get_creators(admin=admin, limit=100, offset=offset, category='users')
                    if not success:
                        raise Exception(msg)
                    creators.extend(msg)

            Utils.write_log(f'=== Messaging started for {task_id} ===')

            while True:
                success, task_status = Utils.check_task_status(task_id)
                if not success:
                    raise Exception(task_status)
                if task_status['status'].lower() in ['cancelled', 'canceled']:
                    break

                tasks = [
                    Creator().send_messages(admin, task_id, creator, scrapers, config, max_actions)
                    for creator in creators
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for success, result in results:
                    if not success:
                        client_msg = {'msg': f'Error messaging creators on {task_id}: {result}', 'status': 'error', 'type': 'message'}
                        success, msg = Utils.update_client(client_msg)
                        if not success:
                            Utils.write_log(msg)
                    Utils.write_log(f'=== {result} ===')

                wait_message = f'Waiting for {time_message[str(time_between)]} before sending another batch of messages'
                Utils.write_log(wait_message)
                client_msg = {'msg': wait_message, 'status': 'success', 'type': 'message'}
                success, msg = Utils.update_client(client_msg)
                if not success:
                    Utils.write_log(msg)
                
                sleep_time = 10
                for _ in range(int(time_between / sleep_time)):
                    print(f'Sleeping for {sleep_time} seconds')
                    success, task_status = Utils.check_task_status(task_id)
                    if not success:raise Exception(task_status)
                    if task_status['status'].lower() in ['cancelled', 'canceled']:
                        raise Cancelled(task_status)
                    await asyncio.sleep(sleep_time)


        except Cancelled as error:
            task_status = task_status['status'] if isinstance(task_status,dict) else task_status
            if  task_status.lower() in ['cancelled', 'canceled']:
                client_msg = {'msg': f'Task | {task_id} has been cancelled', 'status': 'error', 'type': 'message'}
                success, msg = Utils.update_client(client_msg)
                if not success:
                    Utils.write_log(msg)
                Utils.write_log(f'Task | {task_id} was stopped')
            else:
                Utils.write_log(f'Task | {task_id} finished operation')

        except Exception as e:
            Utils.write_log(e)
            task_status = 'failed'
            task_msg = f'Error in messaging | {task_id}: {e}'
            client_msg = {'msg': f'Error in messaging | {task_id}: {e}', 'status': 'error', 'type': 'message'}
            success, msg = Utils.update_client(client_msg)
            if not success:
                Utils.write_log(msg)

            success, msg = Utils.update_task(task_id, {'status': task_status, 'message': task_msg})
            task_data = task
            task_data.update({'updated': str(datetime.now()), 'status': task_status})
            success, msg = Utils.update_client({'task': task_data, 'type': 'task'})
            if not success:
                Utils.write_log(msg)

        finally:
            task_status = task_status['status'] if isinstance(task_status,dict) else task_status
            if  task_status.lower() in ['cancelled', 'canceled']:
                client_msg = {'msg': f'Task | {task_id} has been cancelled', 'status': 'error', 'type': 'message'}
                success, msg = Utils.update_client(client_msg)
                if not success:
                    Utils.write_log(msg)
                Utils.write_log(f'Task | {task_id} was stopped')
            else:
                Utils.write_log(f'Task | {task_id} finished operation')