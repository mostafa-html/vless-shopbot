import uuid
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse
from typing import List, Dict

from py3xui import Api, Client, Inbound

from shop_bot.data_manager.database import get_host, get_key_by_email

logger = logging.getLogger(__name__)


def login_to_host(host_url: str, username: str, password: str, inbound_id: int) -> tuple[Api | None, Inbound | None]:
    try:
        api = Api(host=host_url, username=username, password=password)
        api.login()
        inbounds: List[Inbound] = api.inbound.get_list()
        target_inbound = next((inbound for inbound in inbounds if inbound.id == inbound_id), None)
        if target_inbound is None:
            logger.error(f"Inbound with ID '{inbound_id}' not found on host '{host_url}'")
            return api, None
        return api, target_inbound
    except Exception as e:
        logger.error(f"Login or inbound retrieval failed for host '{host_url}': {e}", exc_info=True)
        return None, None


def get_connection_string(inbound: Inbound, user_uuid: str, host_url: str, remark: str) -> str | None:
    if not inbound:
        return None

    # Guard against missing stream_settings — 3x-ui v3.x may return None
    if not inbound.stream_settings:
        logger.error("stream_settings is None on inbound — check 3x-ui panel config")
        return None

    reality = getattr(inbound.stream_settings, 'reality_settings', None)
    if not reality:
        logger.error("reality_settings missing — ensure inbound uses REALITY security")
        return None

    settings = reality.get('settings')
    if not settings:
        return None

    public_key = settings.get('publicKey')
    fp = settings.get('fingerprint', 'chrome')  # fallback fingerprint for v3.x
    server_names = reality.get('serverNames')
    short_ids = reality.get('shortIds')
    port = inbound.port

    if not all([public_key, server_names, short_ids]):
        return None

    parsed_url = urlparse(host_url)
    short_id = short_ids[0] if short_ids else ''
    return (
        f'vless://{user_uuid}@{parsed_url.hostname}:{port}'
        f'?type=tcp&security=reality&pbk={public_key}&fp={fp}&sni={server_names[0]}'
        f'&sid={short_id}&spx=%2F&flow=xtls-rprx-vision#{remark}'
    )


def _bytes_from_gb(traffic_gb: int) -> int:
    return max(0, int(traffic_gb)) * 1024 * 1024 * 1024


def _set_client_traffic_limit(client_obj, traffic_gb: int) -> int:
    traffic_bytes = _bytes_from_gb(traffic_gb)
    # py3xui 0.5.x canonical attribute is total_gb (stores bytes despite the name)
    if hasattr(client_obj, 'total_gb'):
        client_obj.total_gb = traffic_bytes
    elif hasattr(client_obj, 'totalGB'):
        client_obj.totalGB = traffic_bytes
    else:
        client_obj.__dict__['total'] = traffic_bytes
    return traffic_bytes


def update_or_create_client_on_panel(api: Api, inbound_id: int, email: str, days_to_add: int, traffic_gb: int) -> tuple[str | None, int | None, int | None]:
    try:
        inbound_to_modify = api.inbound.get_by_id(inbound_id)
        if not inbound_to_modify:
            raise ValueError(f'Could not find inbound with ID {inbound_id}')
        if inbound_to_modify.settings.clients is None:
            inbound_to_modify.settings.clients = []
        now_ms = int(datetime.now().timestamp() * 1000)
        client_index = next((i for i, c in enumerate(inbound_to_modify.settings.clients) if c.email == email), -1)
        if client_index != -1:
            existing = inbound_to_modify.settings.clients[client_index]
            current_expiry_ms = getattr(existing, 'expiry_time', 0) or 0
            base_dt = datetime.fromtimestamp(current_expiry_ms / 1000) if current_expiry_ms > now_ms else datetime.now()
            new_expiry_dt = base_dt + timedelta(days=days_to_add)
            existing.expiry_time = int(new_expiry_dt.timestamp() * 1000)
            existing.enable = True
            _set_client_traffic_limit(existing, traffic_gb)
            client_uuid = existing.id
            traffic_bytes = _bytes_from_gb(traffic_gb)
        else:
            client_uuid = str(uuid.uuid4())
            new_expiry_ms = int((datetime.now() + timedelta(days=days_to_add)).timestamp() * 1000)
            new_client = Client(
                id=client_uuid,
                email=email,
                enable=True,
                flow='xtls-rprx-vision',
                expiry_time=new_expiry_ms,
            )
            traffic_bytes = _set_client_traffic_limit(new_client, traffic_gb)
            inbound_to_modify.settings.clients.append(new_client)
        api.inbound.update(inbound_id, inbound_to_modify)
        refreshed = api.inbound.get_by_id(inbound_id)
        refreshed_client = next((c for c in (refreshed.settings.clients or []) if c.email == email), None)
        expiry_ms = getattr(refreshed_client, 'expiry_time', None)
        return client_uuid, expiry_ms, traffic_bytes
    except Exception as e:
        logger.error(f'Error in update_or_create_client_on_panel: {e}', exc_info=True)
        return None, None, None


def create_or_update_key_on_host(host_name: str, email: str, days_to_add: int, traffic_gb: int) -> Dict | None:
    host_data = get_host(host_name)
    if not host_data:
        logger.error(f"Workflow failed: Host '{host_name}' not found in the database.")
        return None
    api, inbound = login_to_host(
        host_url=host_data['host_url'],
        username=host_data['host_username'],
        password=host_data['host_pass'],
        inbound_id=host_data['host_inbound_id']
    )
    if not api or not inbound:
        logger.error(f"Workflow failed: Could not log in or find inbound on host '{host_name}'.")
        return None
    client_uuid, new_expiry_ms, traffic_bytes = update_or_create_client_on_panel(api, inbound.id, email, days_to_add, traffic_gb)
    if not client_uuid:
        logger.error(f"Workflow failed: Could not create/update client '{email}' on host '{host_name}'.")
        return None
    connection_string = get_connection_string(inbound, client_uuid, host_data['host_url'], remark=host_name)
    return {
        'client_uuid': client_uuid,
        'email': email,
        'expiry_timestamp_ms': new_expiry_ms,
        'connection_string': connection_string,
        'host_name': host_name,
        'traffic_limit_bytes': traffic_bytes,
        'traffic_limit_gb': traffic_gb
    }


def get_key_details_from_host(key_data: dict) -> dict | None:
    host_name = key_data.get('host_name')
    if not host_name:
        return None
    host_db_data = get_host(host_name)
    if not host_db_data:
        return None
    api, inbound = login_to_host(
        host_url=host_db_data['host_url'],
        username=host_db_data['host_username'],
        password=host_db_data['host_pass'],
        inbound_id=host_db_data['host_inbound_id']
    )
    if not api or not inbound:
        return None
    connection_string = get_connection_string(inbound, key_data['xui_client_uuid'], host_db_data['host_url'], remark=host_name)
    return {'connection_string': connection_string}


def delete_client_on_host(host_name: str, client_email: str) -> bool:
    host_data = get_host(host_name)
    if not host_data:
        return False
    api, inbound = login_to_host(
        host_url=host_data['host_url'],
        username=host_data['host_username'],
        password=host_data['host_pass'],
        inbound_id=host_data['host_inbound_id']
    )
    if not api or not inbound:
        return False
    try:
        client_to_delete = get_key_by_email(client_email)
        if client_to_delete:
            api.client.delete(inbound.id, client_to_delete['xui_client_uuid'])
            return True
        return True
    except Exception as e:
        logger.error(f"Failed to delete client '{client_email}' from host '{host_name}': {e}", exc_info=True)
        return False
