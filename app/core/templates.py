"""Shared Jinja2Templates instance.

Centralized so all page routes render against the same env (filters, globals,
loader). Importing from here ensures we don't accidentally create multiple
template environments with diverging configuration.
"""

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
