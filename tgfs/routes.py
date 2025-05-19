# TG-FileStream
# Copyright (C) 2025 Deekshith SH

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
from typing import Optional, Union, cast
from aiohttp import web
from telethon.utils import get_input_location
from telethon.tl.custom import Message
from telethon.tl.types import InputPeerUser, Photo, Document

from tgfs.cache_util import lru_cache
from tgfs.streamer import InputTypeLocation
from tgfs.telegram import client, transfer
from tgfs.utils import get_filename, FileInfo

log = logging.getLogger(__name__)
routes = web.RouteTableDef()

@lru_cache(128)
async def get_file(chat_id: int, msg_id: int, expected_name: str) -> Optional[FileInfo]:
    peer = InputPeerUser(user_id=chat_id, access_hash=0)
    message = cast(Message, await client.get_messages(entity=peer, ids=msg_id))
    if not message or not message.file or get_filename(message) != expected_name:
        return None

    media: InputTypeLocation = message.media
    file: Union[Photo, Document] = getattr(media, "document", None) or getattr(media, "photo", None)
    return FileInfo(
        message.file.size,
        message.file.mime_type,
        expected_name,
        file.id,
        *get_input_location(media)
    )

@routes.get(r"/{chat_id:-?\d+}/{msg_id:-?\d+}/{name}")
async def handle_file_request(req: web.Request) -> web.Response:
    head: bool = req.method == "HEAD"
    chat_id = int(req.match_info["chat_id"])
    msg_id = int(req.match_info["msg_id"])
    file_name = req.match_info["name"]
    file: FileInfo = await get_file(chat_id, msg_id, file_name)
    if not file:
        return web.Response(status=404, text="404: Not Found")

    size = file.file_size
    from_bytes = req.http_range.start or 0
    until_bytes = (req.http_range.stop or size) - 1

    if (until_bytes >= size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(status=416,headers={"Content-Range": f"bytes */{size}"})

    if head:
        body = None
    else:
        body = transfer.download(file.location, file.dc_id, size, from_bytes, until_bytes)

    return web.Response(status=200 if (from_bytes == 0 and until_bytes == size - 1) else 206,
    body=body,
    headers={
        "Content-Type": file.mime_type,
        "Content-Range": f"bytes {from_bytes}-{until_bytes}/{size}",
        "Content-Length": str(until_bytes - from_bytes + 1),
        "Content-Disposition": f'attachment; filename="{file_name}"',
        "Accept-Ranges": "bytes",
    })
