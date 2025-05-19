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

from os import environ

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed. Skipping .env loading.")

class Config:
    API_ID: int = int(environ["API_ID"])
    API_HASH: str = environ["API_HASH"]
    BOT_TOKEN: str = environ["BOT_TOKEN"]
    HOST: str = environ.get("HOST", "0.0.0.0")
    PORT: int = environ.get("PORT", 8080)
    PUBLIC_URL: str = environ.get("PUBLIC_URL", f"http://{HOST}:{PORT}")
    DEBUG: bool = True
