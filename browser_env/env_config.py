# websites domain
import os

REDDIT = os.environ.get("REDDIT", "http://localhost:9999")
SHOPPING = os.environ.get("SHOPPING", "http://localhost:7770")
ONESTOPSHOP = os.environ.get("ONESTOPSHOP", "http://localhost:7770")
SHOPPING_ADMIN = os.environ.get("SHOPPING_ADMIN", "http://localhost:7780/admin")
GITLAB = os.environ.get("GITLAB", "http://localhost:8023")
WIKIPEDIA = os.environ.get("WIKIPEDIA", "http://localhost:8888/wikipedia_en_all_maxi_2022-05/A/User:The_other_Kiwix_guy/Landing")
MAP = os.environ.get("MAP", "http://localhost:3000")
HOMEPAGE = os.environ.get("HOMEPAGE", "http://localhost:4399")
TIMEOFF = os.environ.get("TIMEOFF", "http://localhost:3102")
KEYSTONEJS = os.environ.get("KEYSTONEJS", "http://localhost:3100")
NODEBB = os.environ.get("NODEBB", "http://localhost:3101")
POSTMILL = os.environ.get("POSTMILL", "http://localhost:3200")

assert (
    REDDIT
    and SHOPPING
    and ONESTOPSHOP
    and SHOPPING_ADMIN
    and GITLAB
    and WIKIPEDIA
    and MAP
    and HOMEPAGE
    and TIMEOFF
    and KEYSTONEJS
    and NODEBB
    and POSTMILL
), (
    f"Please setup the URLs to each site. Current: \n"
    + f"Reddit: {REDDIT}\n"
    + f"Shopping: {SHOPPING}\n"
    + f"OneStopShop: {ONESTOPSHOP}\n"
    + f"Shopping Admin: {SHOPPING_ADMIN}\n"
    + f"Gitlab: {GITLAB}\n"
    + f"Wikipedia: {WIKIPEDIA}\n"
    + f"Map: {MAP}\n"
    + f"Homepage: {HOMEPAGE}\n"
    + f"TimeOff: {TIMEOFF}\n"
    + f"KeystoneJS: {KEYSTONEJS}\n"
    + f"NodeBB: {NODEBB}\n"
    + f"Postmill: {POSTMILL}\n"
)


ACCOUNTS = {
    "reddit": {"username": "MarvelsGrantMan136", "password": "test1234"},
    "gitlab": {"username": "byteblaze", "password": "hello1234"},
    "shopping": {
        "username": "emma.lopez@gmail.com",
        "password": "Password.123",
    },
    "onestopshop": {
        "username": "emma.lopez@gmail.com",
        "password": "Password.123",
    },
    "shopping_admin": {"username": "admin", "password": "admin1234"},
    "shopping_site_admin": {"username": "admin", "password": "admin1234"},
    "timeoff": {"username": "vector@selab.com", "password": "selab1623"},
    "keystonejs": {"username": "vector@selab.com", "password": "password"},
    "nodebb": {"username": "vector@selab.com", "password": "selab1623"},
    "postmill": {"username": "MarvelsGrantMan136", "password": "test1234"},
}

URL_MAPPINGS = {
    REDDIT: "http://reddit.com",
    SHOPPING: "http://onestopmarket.com",
    ONESTOPSHOP: "http://onestopmarket.com",
    SHOPPING_ADMIN: "http://luma.com/admin",
    GITLAB: "http://gitlab.com",
    WIKIPEDIA: "http://wikipedia.org",
    MAP: "http://openstreetmap.org",
    HOMEPAGE: "http://homepage.com",
    TIMEOFF: "http://timeoff.management",
    KEYSTONEJS: "http://keystonejs.com",
    NODEBB: "http://nodebb.com",
    POSTMILL: "http://postmill.com",
}
