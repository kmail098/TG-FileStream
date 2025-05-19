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

# This file includes a method inspired by tgfilestream by Tulir Asokan (2019)
# https://github.com/tulir/tgfilestream

# pylint: disable=protected-access

import asyncio
import logging
import math
from typing import AsyncGenerator, Dict, Union, cast

from telethon import TelegramClient
from telethon.network import MTProtoSender
from telethon.errors import FloodWaitError, DcIdInvalidError
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest
from telethon.tl.types import InputDocumentFileLocation, InputPhotoFileLocation, DcOption
from telethon.tl.types.upload import File
InputTypeLocation = Union[InputDocumentFileLocation, InputPhotoFileLocation]

mainlog = logging.getLogger(__name__)

class DCManager:
    client: TelegramClient
    lock: asyncio.Lock
    senders:Dict[int, MTProtoSender]
    dc: DcOption
    log: logging.Logger

    def __init__(self, client) -> None:
        self.log = mainlog.getChild("DCManager")
        self.client = client
        self.lock = asyncio.Lock()
        self.senders = {}
        self.dc = None

    async def get_sender(self, dc_id: int) -> MTProtoSender:
        log = self.log
        async with self.lock:
            sender = self.senders.get(dc_id,None)
            if sender:
                log.debug("[DC %d] Using Existing Connection",dc_id)
                return sender
            log.debug("[DC %d] Creating Connection",dc_id)
            auth_key = self.client.session.auth_key if dc_id == self.client.session.dc_id else None
            sender = MTProtoSender(auth_key, loggers=self.client._log)
            log.info("[DC %d]Connecting...", dc_id)
            self.dc = await self.client._get_dc(dc_id)
            connection_info = self.client._connection(self.dc.ip_address, self.dc.port, self.dc.id, loggers=self.client._log, proxy=self.client._proxy)
            await sender.connect(connection_info)
            if not auth_key:
                try:
                    log.info("exporting auth to DC %d", dc_id)
                    auth = await self.client(ExportAuthorizationRequest(self.dc.id))
                    init_request = self.client._init_request
                    init_request.query = ImportAuthorizationRequest(
                        id=auth.id, bytes=auth.bytes
                    )
                    req = InvokeWithLayerRequest(
                        LAYER, init_request
                    )
                    await sender.send(req)
                except DcIdInvalidError:
                    self.log.error("[DC %d] Got DcIdInvalidError", dc_id)

            self.senders[dc_id] = sender
            return sender

class TGStreamer():
    log: logging.Logger
    client: TelegramClient
    dc_manager: DCManager
    def __init__(self, client: TelegramClient) -> None:
        self.log = mainlog.getChild("TGStreamer")
        self.client = client
        self.dc_manager = DCManager(client)

    async def close_senders(self) -> None:
        for _, conn in self.dc_manager.senders.items():
            await conn.disconnect()

    # https://github.com/tulir/tgfilestream/blob/9c04dc727ac6bbeb4dd91a83476803dc1b3c56e1/tgfilestream/paralleltransfer.py#L158
    async def _int_download(self, request: GetFileRequest, first_part: int, last_part: int,
        part_count: int, part_size: int, dc_id: int, first_part_cut: int,
        last_part_cut: int) -> AsyncGenerator[bytes, None]:
        log = self.log
        try:
            part = first_part
            sender = await self.dc_manager.get_sender(dc_id)
            while part <= last_part:
                try:
                    result = cast(File, await sender.send(request))
                except FloodWaitError as e:
                    log.info("Flood wait of %d seconds", e.seconds)
                    await asyncio.sleep(e.seconds)
                    result = await sender.send(request)

                request.offset += part_size
                if not result.bytes:
                    break
                elif first_part == last_part:
                    yield result.bytes[first_part_cut:last_part_cut]
                elif part == first_part:
                    yield result.bytes[first_part_cut:]
                elif part == last_part:
                    yield result.bytes[:last_part_cut]
                else:
                    yield result.bytes
                log.debug(f"Part {part}/{last_part} (total {part_count}) downloaded")
                part += 1
            log.debug("Parallel download finished")
        except (GeneratorExit, StopAsyncIteration, asyncio.CancelledError):
            log.debug("Parallel download interrupted")
            raise
        except Exception:
            log.debug("Parallel download errored", exc_info=True)

    def download(self, location: InputTypeLocation, dc_id: int, file_size: int, from_bytes: int, until_bytes: int) -> AsyncGenerator[bytes, None]:
        part_size = 1024 * 1024 #1MB
        first_part_cut = from_bytes % part_size
        first_part = math.floor(from_bytes / part_size)
        last_part_cut = (until_bytes % part_size) + 1
        last_part = math.ceil(until_bytes / part_size)
        part_count = math.ceil(file_size / part_size)
        self.log.debug(f"Starting parallel download: chunks {first_part}-{last_part}"
                       f" of {part_count} {location!s}")
        request = GetFileRequest(location, offset=first_part * part_size, limit=part_size)

        return self._int_download(request, first_part, last_part, part_count, part_size, dc_id,
                                  first_part_cut, last_part_cut)
