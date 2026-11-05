from .core import register_core
from .characters import register_characters
from .collection import register_collection
from .economy import register_economy
from .games import register_games
from .admin import register_admin
from .marketplace import register_marketplace
from .profiles import register_profiles
from .misc import register_misc

def register_all(app):
    for register in (register_core,register_characters,register_collection,register_economy,register_games,register_admin,register_marketplace,register_profiles,register_misc):
        register(app)
