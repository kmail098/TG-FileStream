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

from typing import Optional, Union, cast
from dataclasses import dataclass
from telethon import TelegramClient
from telethon.utils import get_input_location
from telethon.tl import types
from telethon.tl.custom import Message
from telethon.events import NewMessage

from tgfs.config import Config

InputTypeLocation = Union[types.InputDocumentFileLocation, types.InputPhotoFileLocation]

@dataclass
class FileInfo:
    __slots__ = ("file_size", "mime_type", "file_name", "id", "dc_id", "location")

    file_size: int
    mime_type: str
    file_name: str
    id: int
    dc_id: int
    location: InputTypeLocation


def get_filename(message: Union[Message, NewMessage.Event]) -> str:
    if message.file.name:
        return message.file.name
    media: Union[types.MessageMediaDocument, types.MessageMediaPhoto] = message.media
    file: Union[types.Photo, types.Document] = getattr(media, "document", None) or getattr(media, "photo", None)
    ext = message.file.ext or ""
    return f"{file.id}{ext}"

async def get_fileinfo(client: TelegramClient, msg_id: int, file_name: str) -> Optional[FileInfo]:
    message = cast(Message, await client.get_messages(Config.BIN_CHANNEL, ids=msg_id))
    if not message or not message.file or get_filename(message) != file_name:
        return None
    media: InputTypeLocation = message.media
    file: Union[types.Photo, types.Document] = getattr(media, "document", None) or getattr(media, "photo", None)
    return FileInfo(
        message.file.size,
        message.file.mime_type,
        file_name,
        file.id,
        *get_input_location(media)
    )
